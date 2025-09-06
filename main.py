import mido
from sequencer.sequencer import Sequencer
import sys
import time

# Platform-specific getch
try:
    # Windows
    import msvcrt
    def get_char():
        return msvcrt.getch().decode('utf-8')
except ImportError:
    # POSIX (Linux, macOS)
    import tty, termios
    def get_char():
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch

def print_help():
    """Prints the help message with available commands."""
    help_text = """
Sequencer CLI Commands:
  help                    - Shows this help message.
  add <name> [prog]       - Adds a new MIDI track. `prog` is an optional program number (1-128).
  addaudio <name> <path>  - Adds a new Audio track with the audio file at <path>.
  load <filepath>         - Loads a song from a MIDI file.
  loadproject <basename>  - Loads a full project (MIDI, vports, assignments).
  list                    - Shows all tracks in the current song.
  ports                   - Lists available MIDI input and output ports.
  vport <name>            - Creates a virtual MIDI output port.
  delvport                - Deletes an existing virtual port.
  assign <track_index>    - Assigns a track to an output port from a list of choices.
  assignmetro             - Assigns an output port for the metronome click.
  unassign <track_index>  - Un-assigns a track from its output port.
  cc                      - Sends a MIDI Control Change message to a selected port.
  setaudiocmd <cmd...>    - Sets the command for the external audio player (e.g., mpv --audio-device=jack).
  setbank <track> <msb> [lsb] - Sets the MIDI bank for a track (MSB=CC0, LSB=CC32).
  setch <track> <ch>      - Sets the MIDI channel (1-16) for a track.
  setprog <track> <prog>  - Sets the MIDI program (1-128) for a track.
  volume <track_index>    - Sets the volume for an audio or MIDI track (0.0 to 1.0).
  velocity <track_index>  - Sets the velocity multiplier for a MIDI track (e.g., 1.0).
  mute <track_index>      - Toggles mute for a track.
  solo <track_index>      - Toggles solo for a track.
  rename <index> <new_name> - Renames a track.
  copy                    - Copies a section of a track using 'measure:beat' positions.
  move <track_index>      - Moves a section of a track using 'measure:beat' positions.
  transpose               - Transposes a section of a track using 'measure:beat' positions.
  record <track_index>    - Records MIDI to a track, with 'measure:beat' precision.
  bis                     - Re-records with the last used 'record' settings.
  delete <track_index>    - Deletes a track after confirmation.
  erase <track_index>     - Erases notes from a track using 'measure:beat' positions.
  tempo <bpm>             - Sets the song tempo in beats per minute.
  timesig <num> <den>     - Sets the song time signature (e.g., 4 4).
  save <filepath>         - Saves only the song to a MIDI file.
  saveproject <basename>  - Saves the full project (MIDI, vports, assignments).
  prime                   - Sends current program/bank state to all assigned ports.
  play [start] [end]      - Plays the song. Start/end positions are in 'measure:beat'.
  loop [start] [end]      - Loops a section of the song. Start/end positions are in 'measure:beat'.
  pause                   - Pauses or resumes playback.
  stop                    - Stops playback.
  restart                 - Stops and restarts playback from the beginning.
  metronome <on|off>      - Enables or disables the metronome.
  quit                    - Exits the sequencer.
"""
    print(help_text)

def process_command(user_input, seq):
    if not user_input:
        return True # Continue loop

    parts = user_input.split()
    command = parts[0].lower()
    args = parts[1:]

    if command == "quit":
        if seq.playback_state != "stopped":
            print("Stopping playback before exiting...")
            seq.stop()
        return False # End loop
    elif command == "help":
        print_help()
    elif command == "add":
        if len(args) == 1:
            seq.add_track(name=args[0], track_type='midi')
        elif len(args) == 2:
            prog = int(args[1])
            if not 1 <= prog <= 128:
                print("Error: Program number must be between 1 and 128.")
            else:
                seq.add_track(name=args[0], track_type='midi', instrument=prog - 1)
        else:
            print("Usage: add <name> [program_number]")
    elif command == "addaudio":
        if len(args) == 2:
            seq.add_track(name=args[0], track_type='audio', filepath=args[1])
        else:
            print("Usage: addaudio <name> <filepath>")
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
    elif command == "delvport":
        if not seq.virtual_ports:
            print("No virtual ports to delete.")
            return True

        print("Available virtual ports:")
        for i, vp in enumerate(seq.virtual_ports):
            print(f"  [{i}] {vp.name}")

        try:
            idx = int(input("Choose a virtual port to delete: "))
            if 0 <= idx < len(seq.virtual_ports):
                seq.delete_virtual_port(seq.virtual_ports[idx].name)
            else:
                print("Error: Invalid index.")
        except (ValueError, IndexError):
            print("Error: Invalid input.")

    elif command == "ports":
        print(seq.list_ports())
    elif command == "assign":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                if not 0 <= track_index < len(seq.song.tracks):
                    print("Error: Invalid track index.")
                    return True

                hardware_ports = mido.get_output_names() # type: ignore
                virtual_port_names = [vp.name for vp in seq.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names

                if not all_outputs:
                    print("No output ports available.")
                    return True

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
    elif command == "assignmetro":
        hardware_ports = mido.get_output_names() # type: ignore
        virtual_port_names = [vp.name for vp in seq.virtual_ports]
        all_outputs = hardware_ports + virtual_port_names

        if not all_outputs:
            print("No output ports available.")
            return True

        print("Available output ports:")
        for i, name in enumerate(all_outputs):
            print(f"  [{i}] {name}")

        try:
            port_index = int(input("Choose a port to assign for the metronome: "))
            if 0 <= port_index < len(all_outputs):
                port_name = all_outputs[port_index]
                seq.song.metronome_port_name = port_name
                print(f"Metronome assigned to port '{port_name}'.")
            else:
                print("Error: Invalid port index.")
        except (ValueError, IndexError):
            print("Error: Invalid input.")
    elif command == "unassign":
        if len(args) == 1:
            seq.unassign_port(track_index=int(args[0]))
        else:
            print("Usage: unassign <track_index>")
    elif command == "setaudiocmd":
        if args:
            cmd_str = " ".join(args)
            seq.audio_player_command = cmd_str
            print(f"Audio player command set to: {cmd_str}")
            print("Note: The audio filepath will be appended to this command.")
        else:
            print("Usage: setaudiocmd <command...>")
            print(f"Current command: {seq.audio_player_command}")
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
            else:
                seq.set_program(track_index=int(args[0]), program=prog - 1)
        else:
            print("Usage: setprog <track_index> <program>")
    elif command == "volume":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                volume_str = input("Enter volume (0.0 - 1.0): ").strip()
                volume = float(volume_str)
                seq.set_track_volume(track_index, volume)
            except ValueError:
                print("Error: Invalid track index or volume.")
        else:
            print("Usage: volume <track_index>")
    elif command == "velocity":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                velocity_str = input("Enter velocity multiplier (e.g., 1.0): ").strip()
                velocity = float(velocity_str)
                seq.set_track_velocity(track_index, velocity)
            except ValueError:
                print("Error: Invalid track index or velocity.")
        else:
            print("Usage: velocity <track_index>")
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
            try:
                track_index = int(args[0])
                seq.record_track(track_index)
            except ValueError:
                print("Error: Invalid track index.")
        else:
            print("Usage: record <track_index>")
    elif command == "bis":
        seq.record_bis()
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
    elif command == "erase":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                # The sequencer method will now handle all prompting
                seq.erase_track(track_index=track_index)
            except ValueError:
                print("Error: Invalid track index.")
        else:
            print("Usage: erase <track_index>")
    elif command == "rename":
        if len(args) == 2:
            seq.rename_track(track_index=int(args[0]), new_name=args[1])
        else:
            print("Usage: rename <track_index> <new_name>")
    elif command == "move":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                seq.move_track_section(track_index)
            except ValueError:
                print("Error: Invalid track index.")
        else:
            print("Usage: move <track_index>")
    elif command == "copy":
        seq.copy_track_section()
    elif command == "transpose":
        seq.transpose_track_section()
    elif command == "tempo":
        if len(args) == 1:
            seq.set_tempo(tempo=int(args[0]))
        else:
            print("Usage: tempo <bpm>")
    elif command == "timesig":
        if len(args) == 2:
            seq.set_time_signature(numerator=int(args[0]), denominator=int(args[1]))
        else:
            print("Usage: timesig <numerator> <denominator>")
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
    elif command == "cc":
        if args:
            print("Usage: cc (command is interactive)")
            return True

        hardware_ports = mido.get_output_names() # type: ignore
        virtual_port_names = [vp.name for vp in seq.virtual_ports]
        all_outputs = hardware_ports + virtual_port_names

        if not all_outputs:
            print("No MIDI output ports available.")
            return True

        print("Available output ports:")
        for i, name in enumerate(all_outputs):
            print(f"  [{i}] {name}")

        try:
            port_index_str = input("Choose a port to send the CC message to: ")
            port_index = int(port_index_str)
            if not 0 <= port_index < len(all_outputs):
                print("Error: Invalid port index.")
                return True
            port_name = all_outputs[port_index]

            channel_str = input("Enter MIDI channel (1-16): ")
            channel = int(channel_str)
            if not 1 <= channel <= 16:
                print("Error: Channel must be between 1 and 16.")
                return True

            control_str = input("Enter CC number (0-127): ")
            control = int(control_str)
            if not 0 <= control <= 127:
                print("Error: CC number must be between 0 and 127.")
                return True

            value_str = input("Enter CC value (0-127): ")
            value = int(value_str)
            if not 0 <= value <= 127:
                print("Error: CC value must be between 0 and 127.")
                return True

            # The sequencer method will handle channel conversion (1-16 -> 0-15)
            seq.send_cc_message(port_name, channel, control, value)

        except (ValueError, IndexError):
            print("Error: Invalid input.")

    elif command == "prime":
        seq.prime_all_tracks()
    elif command == "play" or command == "loop":
        try:
            if len(args) > 2:
                print(f"Usage: {command} [start_position] [end_position]")
                return True

            start_beat_str = args[0] if len(args) >= 1 else None
            start_beat = seq.parse_position_to_beats(start_beat_str) if start_beat_str else None
            if start_beat_str and start_beat is None: # Handle parsing error
                return True

            end_beat_str = args[1] if len(args) >= 2 else None
            end_beat = seq.parse_position_to_beats(end_beat_str, default="") if end_beat_str else None
            if end_beat_str and end_beat is None: # Handle parsing error
                return True

            is_looping = command == "loop"
            seq.play(start_beat=start_beat, end_beat=end_beat, loop=is_looping)

        except Exception as e:
            print(f"Error during command execution: {e}")
    elif command == "pause":
        seq.pause()
    elif command == "stop":
        seq.stop()
    elif command == "restart":
        seq.restart()
    elif command == "metronome":
        if len(args) == 1 and args[0].lower() in ["on", "off"]:
            is_enabled = args[0].lower() == "on"
            seq.song.metronome_enabled = is_enabled
            status = "enabled" if is_enabled else "disabled"
            print(f"Metronome is now {status}.")
        else:
            print("Usage: metronome <on|off>")
    else:
        print(f"Unknown command: '{command}'. Type 'help' for a list of commands.")
    return True

def main():
    """The main entry point for the CLI application."""
    print("Welcome to the Python MIDI Sequencer!")
    seq = Sequencer()
    print_help()

    command_buffer = ""
    print("> ", end="", flush=True)

    while True:
        try:
            char = get_char()

            # Handle Ctrl+C or Ctrl+D for exit
            if char in ('\x03', '\x04'):
                if seq.playback_state != "stopped":
                    print("\nStopping playback before exiting...")
                    seq.stop()
                break

            elif char == ' ':
                if seq.playback_state != "stopped":
                    # If playing, spacebar is a shortcut for pause
                    print("\r" + " " * (len(command_buffer) + 2) + "\r", end="")
                    print("> Pausing...", end="", flush=True)
                    seq.pause()
                    time.sleep(0.5)
                    print("\r" + " " * (len("> Pausing...") + 2) + "\r", end="")
                    print(f"> {command_buffer}", end="", flush=True)
                else:
                    # Otherwise, it's a normal character
                    command_buffer += ' '
                    print(' ', end="", flush=True)
                continue

            elif char in ('\r', '\n'):
                print()  # Move to the next line
                if not process_command(command_buffer, seq):
                    break # Exit if process_command returns False (for 'quit')
                command_buffer = ""
                print("> ", end="", flush=True)

            elif char in ('\x7f', '\b'): # Handle backspace
                if len(command_buffer) > 0:
                    command_buffer = command_buffer[:-1]
                    print("\b \b", end="", flush=True) # Erase character on screen

            elif char.isprintable():
                command_buffer += char
                print(char, end="", flush=True)

        except (ValueError, IndexError) as e:
            print(f"\nError: Invalid argument. Please check your input. ({e})")
            command_buffer = ""
            print(f"> {command_buffer}", end="", flush=True)
        except Exception as e:
            print(f"\nAn unexpected error occurred: {e}")
            command_buffer = ""
            print(f"> {command_buffer}", end="", flush=True)

    # Clean up before exiting
    print("\nExiting sequencer. Goodbye!")
    seq.close_virtual_ports()

if __name__ == "__main__":
    main()
