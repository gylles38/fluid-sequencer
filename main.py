from sequencer.sequencer import Sequencer

def print_help():
    """Prints the help message with available commands."""
    help_text = """
Sequencer CLI Commands:
  help                    - Shows this help message.
  add <name> [instr]      - Adds a new track. `instr` is an optional MIDI program number (0-127).
  load <filepath>         - Loads a MIDI file into a new track.
  list                    - Shows all tracks in the current song.
  record <track_index>    - Records MIDI input into the specified track.
  tempo <bpm>             - Sets the song tempo in beats per minute.
  save <filepath>         - Saves the entire song to a MIDI file.
  play                    - Plays the current song.
  pause                   - Pauses or resumes playback.
  stop                    - Stops playback.
  quit                    - Exits the sequencer.
"""
    print(help_text)

def main():
    """The main entry point for the CLI application."""
    print("Welcome to the Python MIDI Sequencer!")
    seq = Sequencer()
    print_help()

    while True:
        try:
            user_input = input("> ").strip()
            if not user_input:
                continue

            parts = user_input.split()
            command = parts[0].lower()
            args = parts[1:]

            if command == "quit":
                if seq.playback_state != "stopped":
                    print("Stopping playback before exiting...")
                    seq.stop()
                break
            elif command == "help":
                print_help()
            elif command == "add":
                if len(args) == 1:
                    seq.add_track(name=args[0])
                elif len(args) == 2:
                    seq.add_track(name=args[0], instrument=int(args[1]))
                else:
                    print("Usage: add <name> [instrument_id]")
            elif command == "load":
                if len(args) == 1:
                    seq.load_track_from_file(filepath=args[0])
                else:
                    print("Usage: load <filepath>")
            elif command == "list":
                print(seq.list_tracks())
            elif command == "record":
                if len(args) == 1:
                    seq.record_track(track_index=int(args[0]))
                else:
                    print("Usage: record <track_index>")
            elif command == "delete":
                if len(args) == 1:
                    track_index = int(args[0])
                    if 0 <= track_index < len(seq.song.tracks):
                        track_name = seq.song.tracks[track_index].name
                        confirm = input(f"Are you sure you want to delete track '{track_name}'? [y/N] ").lower()
                        if confirm == 'y':
                            seq.delete_track(track_index)
                        else:
                            print("Deletion cancelled.")
                    else:
                        print("Error: Invalid track index.")
                else:
                    print("Usage: delete <track_index>")
            elif command == "tempo":
                if len(args) == 1:
                    seq.set_tempo(tempo=int(args[0]))
                else:
                    print("Usage: tempo <bpm>")
            elif command == "save":
                if len(args) == 1:
                    seq.save_song(filepath=args[0])
                else:
                    print("Usage: save <filepath>")
            elif command == "play":
                seq.play()
            elif command == "pause":
                seq.pause()
            elif command == "stop":
                seq.stop()
            else:
                print(f"Unknown command: '{command}'. Type 'help' for a list of commands.")

        except (ValueError, IndexError) as e:
            print(f"Error: Invalid argument. Please check your input. ({e})")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

    print("Exiting sequencer. Goodbye!")

if __name__ == "__main__":
    main()
