import os
os.environ['KIVY_NO_ARGS'] = '1'
os.environ['KIVY_NO_CONSOLELOG'] = '1'
os.environ['KIVY_BACKEND'] = 'sdl2'
from kivy.config import Config
Config.set('graphics', 'width', '1024')
Config.set('graphics', 'height', '768')
Config.set('graphics', 'headless', '1')

import sys
sys.path.append('src')

from kivy.app import App
from sequencer.kivy_ui import SequencerLayout
from kivy.clock import Clock
import time

class SimulatorApp(App):
    def build(self):
        self.layout = SequencerLayout()
        return self.layout

    def on_start(self):
        Clock.schedule_once(self.run_simulation, 1)

    def run_simulation(self, dt):
        print("Starting simulation...")
        # 1. Add a MIDI track
        self.layout.process_command_ui('add "TestTrack"')

        # 2. Wait for UI to update
        Clock.schedule_once(self.open_editor, 0.5)

    def open_editor(self, dt):
        # 3. Open Editor
        track_widget = self.layout.track_widgets[0]
        track_widget.open_piano_roll_editor()

        # 4. Simulate mode switching
        Clock.schedule_once(self.switch_modes, 0.5)

    def switch_modes(self, dt):
        if not self.layout.window_manager.children:
            print("Error: Editor not found!")
            self.stop()
            return

        editor = self.layout.window_manager.children[0]
        modes = ['insert', 'move', 'delete']

        def do_switch(count):
            if count > 300:
                print("Simulation finished successfully!")
                self.stop()
                return

            mode = modes[count % 3]
            btn = editor.mode_buttons[mode]
            # print(f"Switching to {mode} ({count})")
            editor.set_edit_mode(mode, btn)

            # Simulate mouse movement too
            editor._on_mouse_pos(None, (500 + (count % 100), 400 + (count % 100)))

            Clock.schedule_once(lambda d: do_switch(count + 1), 0.01)

        do_switch(0)

if __name__ == '__main__':
    SimulatorApp().run()
