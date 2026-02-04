import os
os.environ['KIVY_NO_ARGS'] = '1'
os.environ['KIVY_NO_CONSOLELOG'] = '1'
try:
    from sequencer.ui_components.piano_roll_editor import PianoRollEditor
    print("PianoRollEditor imported successfully.")
    # Check if scroll_to_beat exists in the class
    if hasattr(PianoRollEditor, 'scroll_to_beat'):
        print("PianoRollEditor has scroll_to_beat method.")
    else:
        print("PianoRollEditor MISSING scroll_to_beat method.")
except Exception as e:
    print(f"Error during import: {e}")
