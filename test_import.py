import sys
import os
sys.path.append('src')
try:
    from sequencer.ui_components.piano_roll_editor import EditableMidiGrid
    import sequencer.ui_components.piano_roll_editor as pre
    print(f"Line in pre: {'Line' in dir(pre)}")
except Exception as e:
    print(f"Error: {e}")
