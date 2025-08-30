import mido
from sequencer.sequencer import Sequencer

def print_help():
    """Prints the help message with available commands."""
    help_text = """
Sequencer CLI Commands:
  help                    - Shows this help message.
  add <name> [prog]       - Adds a new track. `prog` is an optional program number (1-128).
  load <filepath>         - Loads a song from a MIDI file.
  loadproject <basename>  - Loads a full project (MIDI, vports, assignments).
  list                    - Shows all tracks in the current song.
  ports                   - Lists available MIDI input and output ports.
  vport <name>            - Creates a virtual MIDI output port for use with other apps.
  assign <track_index>    - Assigns a track to an output port from a list of choices.
  setbank <track> <msb> [lsb] - Sets the MIDI bank for a track (MSB=CC0, LSB=CC32).
  setch <track> <ch>      - Sets the MIDI channel (1-16) for a track.
  setprog <track> <prog>  - Sets the MIDI program (1-128) for a track.
  mute <track_index>      - Toggles mute for a track.
  solo <track_index>      - Toggles solo for a track.
  record <track_index>    - Records MIDI to a track, with optional live MIDI thru.
  delete <track_index>    - Deletes a track after confirmation.
  tempo <bpm>             - Sets the song tempo in beats per minute.
  save <filepath>         - Saves only the song to a MIDI file.
  saveproject <basename>  - Saves the full project (MIDI, vports, assignments).
  prime                   - Sends current program/bank state to all assigned ports.
  play                    - Plays the song using the assigned ports for each track.
  pause                   - Pauses or resumes playback.
  stop                    - Stops playback.
  restart                 - Stops and restarts playback from the beginning.
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
                    prog = int(args[1])
                    if not 1 <= prog <= 128:
                        print("Error: Program number must be between 1 and 128.")
                        continue
                    seq.add_track(name=args[0], instrument=prog - 1)
                else:
                    print("Usage: add <name> [program_number]")
            elif command == "load":
                if len(args) == 1:
                    confirm = input("Loading a new song will discard the current session. Are you sure? [y/N] ").lower()
                    if confirm == 'y':
                        seq.load_song(filepath=args[0])
                    else:
                        print("Load cancelled.")
                else:
                    print("Usage: load <filepath>")
            elif command == "loadproject":
                if len(args) == 1:
                    confirm = input("Loading a new project will discard the current session. Are you sure? [y/N] ").lower()
                    if confirm == 'y':
                        seq.load_project(basename=args[0])
                    else:
                        print("Load cancelled.")
                else:
                    print("Usage: loadproject <basename>")
            elif command == "list":
                print(seq.list_tracks())
            elif command == "vport":
                if len(args) == 1:
                    seq.create_virtual_port(name=args[0])
                else:
                    print("Usage: vport <port_name>")
            elif command == "ports":
                print(seq.list_ports())
            elif command == "assign":
                if len(args) == 1:
                    try:
                        track_index = int(args[0])
                        if not 0 <= track_index < len(seq.song.tracks):
                            print("Error: Invalid track index.")
                            continue

                        hardware_ports = mido.get_output_names()
                        virtual_port_names = [vp.name for vp in seq.virtual_ports]
                        all_outputs = hardware_ports + virtual_port_names

                        if not all_outputs:
                            print("No output ports available.")
                            continue

                        print("Available output ports:")
                        for i, name in enumerate(all_outputs):
                            print(f"  [{i}] {name}")

                        port_index = int(input("Choose a port to assign: "))
                        if 0 <= port_index < len(all_outputs):
                            port_name = all_outputs[port_index]
                            seq.assign_port(track_index, port_name)
                        else:
                            print("Error: Invalid port index.")

                    except (ValueError, IndexError):
                        print("Error: Invalid input.")
                else:
                    print("Usage: assign <track_index>")
            elif command == "setbank":
                if len(args) == 2:
                    seq.set_bank(track_index=int(args[0]), msb=int(args[1]))
                elif len(args) == 3:
                    seq.set_bank(track_index=int(args[0]), msb=int(args[1]), lsb=int(args[2]))
                else:
                    print("Usage: setbank <track_index> <msb> [lsb]")
            elif command == "setch":
                if len(args) == 2:
                    seq.set_channel(track_index=int(args[0]), channel=int(args[1]))
                else:
                    print("Usage: setch <track_index> <channel>")
            elif command == "setprog":
                if len(args) == 2:
                    prog = int(args[1])
                    if not 1 <= prog <= 128:
                        print("Error: Program number must be between 1 and 128.")
                        continue
                    seq.set_program(track_index=int(args[0]), program=prog - 1)
                else:
                    print("Usage: setprog <track_index> <program>")
            elif command == "mute":
                if len(args) == 1:
                    seq.toggle_mute(track_index=int(args[0]))
                else:
                    print("Usage: mute <track_index>")
            elif command == "solo":
                if len(args) == 1:
                    seq.toggle_solo(track_index=int(args[0]))
                else:
                    print("Usage: solo <track_index>")
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
            elif command == "saveproject":
                if len(args) == 1:
                    seq.save_project(basename=args[0])
                else:
                    print("Usage: saveproject <basename>")
            elif command == "prime":
                seq.prime_all_tracks()
            elif command == "play":
                if len(args) == 0:
                    seq.play()
                else:
                    print("Usage: play (takes no arguments)")
            elif command == "pause":
                seq.pause()
            elif command == "stop":
                seq.stop()
            elif command == "restart":
                seq.restart()
            else:
                print(f"Unknown command: '{command}'. Type 'help' for a list of commands.")

        except (ValueError, IndexError) as e:
            print(f"Error: Invalid argument. Please check your input. ({e})")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

    # Clean up before exiting
    seq.close_virtual_ports()
    print("Exiting sequencer. Goodbye!")

if __name__ == "__main__":
    main()
