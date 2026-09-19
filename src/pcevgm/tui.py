"""Curses front end for the VGM debugger."""

from __future__ import annotations

import bisect
import curses
import locale
import time

from .huc6280 import (
    AMPLITUDE_MASK,
    FIRST_NOISE_CHANNEL,
    NUM_CHANNELS,
    WAVE_LENGTH,
    WAVE_ROWS,
    db_fraction,
    plot_width,
    lfo_depth_factor,
    meter,
    note_text,
    wave_rows,
)

from . import keyboard
from . import tracker
from .notes import analyse
from .player import HUC6280_WRITE, Timeline, build_descriptions
from .vgm import SAMPLE_RATE, VgmFile

# Widths of the channel table fields, so the meters land under their numbers.
LEAD_WIDTH = 37  # everything up to the dB L column
LEVEL_WIDTH = 6  # the dB L and dB R columns
AMP_WIDTH = 3
TAIL_WIDTH = 8  # the gap, the BAL column and the gap before WAVE
HEAD_WIDTH = LEAD_WIDTH + 2 * LEVEL_WIDTH + AMP_WIDTH + 4 + TAIL_WIDTH  # 64
ENVELOPE_GAP = 2  # blank columns between the wave plot and the envelope plot
ENVELOPE_LEFT = HEAD_WIDTH + plot_width(WAVE_LENGTH) + ENVELOPE_GAP
# 12 columns is 24 envelope steps, which holds 85% of notes whole.
ENVELOPE_WIDTH = 12
ENVELOPE_CUT = ">"  # the envelope runs on past the panel
TRACKER_GAP = 2
TRACKER_LEFT = ENVELOPE_LEFT + ENVELOPE_WIDTH + TRACKER_GAP
# The panel drops the instrument column when the terminal cannot hold it.
TRACKER_NARROW_COLUMNS = TRACKER_LEFT + tracker.panel_width() + 1
TRACKER_WIDE_COLUMNS = TRACKER_LEFT + tracker.panel_width(wide=True) + 1
UNKNOWN_WAVE = "wave  --"  # the table matches no complete upload
UNKNOWN_INSTRUMENT = "inst  --"  # no note detected on this channel now
UNKNOWN_ENVELOPE = "env  --"

FRAME_SAMPLES = 735  # one NTSC video frame
SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0]
POLL_MS = 20

# Fallback for escape sequences curses hands over one byte at a time.
ARROWS = {
    ord("A"): curses.KEY_UP,
    ord("B"): curses.KEY_DOWN,
    ord("C"): curses.KEY_RIGHT,
    ord("D"): curses.KEY_LEFT,
}

PAIR_TITLE = 1
PAIR_ACTIVE = 2
PAIR_DIM = 3
PAIR_CURSOR = 4
PAIR_WARN = 5
PAIR_KEY_WHITE = 6
PAIR_KEY_BLACK = 7
PAIR_KEY_FIRST = 10  # one pair per channel, the channel colour as background
PAIR_CHANNEL_FIRST = 20  # the same colours as foreground, for the CH column

# One colour per channel, so the keyboard and the channel table agree.
CHANNEL_COLORS = (
    curses.COLOR_RED,
    curses.COLOR_GREEN,
    curses.COLOR_YELLOW,
    curses.COLOR_BLUE,
    curses.COLOR_MAGENTA,
    curses.COLOR_CYAN,
)
KEY_LEFT = 1  # indent of the keyboard
KEYBOARD_ROWS = keyboard.TOTAL_ROWS + 2  # the keys, the octave labels, a blank
CHANNEL_ROWS = 1 + NUM_CHANNELS * WAVE_ROWS + 3  # header, channels, master, LFO
HEADER_ROWS = 6

HELP_LINES = [
    "space      play / pause",
    "left right step one video frame (735 samples)",
    "< >        step one second",
    ", .        step one command",
    "p n        step one HuC6280 register write",
    "g G        go to start / end",
    "l          go to the loop point",
    "[ ]        slower / faster",
    "k          show or hide the keyboard",
    "t          show or hide the tracker",
    "h          log: all commands / HuC6280 writes only",
    "?          show or hide this help",
    "q          quit",
]


def format_time(sample: int) -> str:
    seconds = sample / SAMPLE_RATE
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


class Debugger:
    def __init__(self, vgm: VgmFile) -> None:
        self.vgm = vgm
        self.timeline = Timeline(vgm)
        self.descriptions = build_descriptions(vgm)
        # Lets a playing channel be matched to its --extract-waves file and to
        # the instrument the analysis found.
        self.analysis = analyse(vgm)
        self.wave_names = self.analysis.wave_names
        self.rows = tracker.build(vgm, self.analysis)
        self._limit = None  # columns the left hand panes may use
        self.playing = False
        self.speed_index = 2
        self.writes_only = False
        self.show_keyboard = True
        self.show_tracker = True
        self.show_help = False
        self.play_position = 0.0
        self.status = ""

    @property
    def speed(self) -> float:
        return SPEEDS[self.speed_index]

    # --- drawing -----------------------------------------------------------

    def _put(self, screen, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = screen.getmaxyx()
        if self._limit is not None:
            width = min(width, self._limit)
        if y < 0 or y >= height or x >= width:
            return
        try:
            screen.addnstr(y, x, text, width - x, attr)
        except curses.error:
            pass  # writing the last cell of the last line always raises

    def _draw_header(self, screen) -> int:
        header = self.vgm.header
        clock = self.timeline.clock
        chips = ", ".join(f"{name} {value}" for name, value in header.clocks.items())
        self._put(
            screen,
            0,
            0,
            f" {self.vgm.path}  VGM {header.version_text}  clock {clock} Hz ",
            curses.color_pair(PAIR_TITLE) | curses.A_BOLD,
        )
        self._put(screen, 1, 1, f"chips: {chips or 'none declared'}", curses.color_pair(PAIR_DIM))

        gd3 = self.vgm.gd3
        if gd3 and (gd3.track or gd3.game or gd3.author):
            tag = " / ".join(part for part in (gd3.game, gd3.track, gd3.author) if part)
            self._put(screen, 2, 1, tag, curses.color_pair(PAIR_DIM))

        state = "PLAY " if self.playing else "PAUSE"
        transport = (
            f"{state}  x{self.speed:<4g} "
            f"{format_time(self.timeline.sample)} / {format_time(self.timeline.end_sample)}"
            f"   sample {self.timeline.sample}"
            f"   cmd {self.timeline.index}/{len(self.timeline.commands)}"
        )
        self._put(screen, 3, 1, transport, curses.A_BOLD)

        width = screen.getmaxyx()[1]
        bar_width = max(4, width - 4)
        done = self.timeline.sample / self.timeline.end_sample if self.timeline.end_sample else 0
        filled = int(bar_width * min(1.0, done))
        self._put(screen, 4, 1, "█" * filled, curses.color_pair(PAIR_ACTIVE))
        self._put(screen, 4, 1 + filled, "─" * (bar_width - filled), curses.color_pair(PAIR_DIM))
        return 6

    def _identity(self, index: int, channel):
        """What the channel is playing: instrument, then its wave and envelope."""
        found = self.analysis.instrument_at(index, self.timeline.sample)
        if found is None:
            instrument, envelope = UNKNOWN_INSTRUMENT, UNKNOWN_ENVELOPE
        else:
            instrument = self.analysis.instrument_names[found]
            _, envelope_id = self.analysis.instruments[found]
            envelope = self.analysis.envelope_names[envelope_id]
        # The wave comes from the chip, not the instrument, so a table
        # re-uploaded part way through a note still shows.
        wave = self.wave_names.get(bytes(channel.waveform), UNKNOWN_WAVE)
        return instrument, wave, envelope

    def _channel_text(self, index: int, state):
        """Head line, meter line, extras line and one wave string per row."""
        channel = state.channels[index]
        hz = channel.frequency_hz(state.clock)
        levels = channel.levels_db(state.master_left, state.master_right)
        if channel.enabled:
            flag = "DDA" if channel.dda else "ON "
        else:
            flag = "off"
        if levels is None:
            left = right = "    --"
        else:
            left, right = f"{levels[0]:6.1f}", f"{levels[1]:6.1f}"

        head = (
            f" {index:2d}  {flag}  0x{channel.frequency:03X}  {hz:8.1f}  "
            f"{note_text(hz):<8}  {left}  {right}  "
            f"{channel.amplitude:3d}  {channel.balance_left:X}/{channel.balance_right:X}   "
        )

        lead = "      " + "   ".join(self._identity(index, channel))
        lead = lead[:LEAD_WIDTH].ljust(LEAD_WIDTH)

        extras = []
        if channel.dda:
            extras.append(f"dda {channel.dda_sample:2d}")
        if index >= FIRST_NOISE_CHANNEL:
            extras.append(
                f"noise on {channel.noise_frequency:2d}"
                if channel.noise_enabled
                else "noise off"
            )
        extra = ""
        if extras:
            extra = ("      " + "   ".join(extras)).ljust(HEAD_WIDTH)[:HEAD_WIDTH]

        if levels is None:
            bars = [" " * LEVEL_WIDTH, " " * LEVEL_WIDTH]
        else:
            bars = [meter(db_fraction(value), LEVEL_WIDTH) for value in levels]
        bars.append(meter(channel.amplitude / AMPLITUDE_MASK, AMP_WIDTH))
        detail = lead + "  ".join(bars) + " " * TAIL_WIDTH

        return head, detail, extra, wave_rows(channel.waveform)

    def _key_attr(self, cell) -> int:
        if cell.channel is not None:
            return curses.color_pair(PAIR_KEY_FIRST + cell.channel) | curses.A_BOLD
        return curses.color_pair(PAIR_KEY_BLACK if cell.black else PAIR_KEY_WHITE)

    def _draw_keyboard(self, screen, top: int) -> int:
        state = self.timeline.state
        for offset, cells in enumerate(keyboard.render(state)):
            for column, cell in enumerate(cells):
                self._put(screen, top + offset, KEY_LEFT + column, cell.char,
                          self._key_attr(cell))
        labels = top + keyboard.TOTAL_ROWS
        self._put(screen, labels, KEY_LEFT, keyboard.labels(), curses.color_pair(PAIR_DIM))
        aside = "  ".join(f"ch{index} dda" for index in keyboard.sample_channels(state))
        self._put(screen, labels, KEY_LEFT + keyboard.WIDTH + 2, aside,
                  curses.color_pair(PAIR_DIM))
        return top + KEYBOARD_ROWS

    def _envelope_plot(self, index: int):
        shape, step = self.analysis.envelope_at(index, self.timeline.sample)
        return (None, 0) if shape is None else (wave_rows(shape), step)

    def _draw_channels(self, screen, top: int) -> int:
        columns = (
            f" {'CH':>2}  {'ST':<3}  {'DIV':<5}  {'HZ':>8}  {'NOTE':<8}  "
            f"{'dB L':>6}  {'dB R':>6}  {'AMP':>3}  {'BAL':<3}   WAVE"
        ).ljust(ENVELOPE_LEFT) + "ENVELOPE"
        self._put(screen, top, 0, columns, curses.A_UNDERLINE | curses.A_BOLD)

        state = self.timeline.state
        row = top + 1
        for index in range(NUM_CHANNELS):
            head, detail, extra, plot = self._channel_text(index, state)
            channel = state.channels[index]
            attr = (
                curses.color_pair(PAIR_ACTIVE)
                if channel.enabled
                else curses.color_pair(PAIR_DIM)
            )
            if index == state.selected:
                attr |= curses.A_BOLD
            # The head sits on the first row, the detail under it. The plot
            # spans every row, so it keeps the channel colour throughout.
            prefix = (head, detail, extra)
            for line in range(WAVE_ROWS):
                if line < len(prefix) and prefix[line]:
                    self._put(screen, row + line, 0, prefix[line],
                              attr if line == 0 else curses.color_pair(PAIR_DIM))
                self._put(screen, row + line, HEAD_WIDTH, plot[line], attr)
            # Tint the channel number to match its key on the keyboard.
            self._put(screen, row, 1, f"{index:2d}",
                      curses.color_pair(PAIR_CHANNEL_FIRST + index) | curses.A_BOLD)
            envelope, step = self._envelope_plot(index)
            if envelope is not None:
                full = len(envelope[0])
                shown = min(full, ENVELOPE_WIDTH)
                # One character covers two steps, so the cursor does too.
                cursor = min(step // 2, shown - 1)
                for line in range(WAVE_ROWS):
                    self._put(screen, row + line, ENVELOPE_LEFT,
                              envelope[line][:shown], attr)
                    self._put(screen, row + line, ENVELOPE_LEFT + cursor,
                              envelope[line][cursor], attr | curses.A_REVERSE)
                if full > ENVELOPE_WIDTH:
                    self._put(screen, row + WAVE_ROWS // 2,
                              ENVELOPE_LEFT + ENVELOPE_WIDTH - 1, ENVELOPE_CUT,
                              curses.color_pair(PAIR_DIM))
            row += WAVE_ROWS

        master = (
            f" master balance L={state.master_left:X} R={state.master_right:X}"
            f"   selected ch{state.selected}"
            f"   writes {state.writes}"
        )
        lfo = (
            f" LFO  enabled {'yes' if state.lfo_enabled else 'no '}"
            f"   depth {state.lfo_depth} (x{lfo_depth_factor(state.lfo_depth)})"
            f"   freq {state.lfo_frequency}"
            f"   ctrl 0x{state.lfo_control:02X}"
        )
        self._put(screen, row, 0, master, curses.color_pair(PAIR_DIM))
        attr = 0 if state.lfo_enabled and state.lfo_depth else curses.color_pair(PAIR_DIM)
        self._put(screen, row + 1, 0, lfo, attr)
        return row + 3

    def _visible_log_indices(self, rows: int):
        commands = self.timeline.commands
        cursor = min(self.timeline.index, max(0, len(commands) - 1))
        if self.writes_only:
            pool = self.timeline.write_indices
            if not pool:
                return []
            position = bisect.bisect_left(pool, cursor)
            start = max(0, min(position - rows // 2, len(pool) - rows))
            return pool[start : start + rows]
        start = max(0, min(cursor - rows // 2, len(commands) - rows))
        return range(start, min(len(commands), start + rows))

    def _draw_log(self, screen, top: int, rows: int) -> None:
        if rows <= 1:
            return
        label = "HuC6280 writes" if self.writes_only else "all commands"
        self._put(screen, top, 0, f" LOG ({label})", curses.A_UNDERLINE | curses.A_BOLD)
        commands = self.timeline.commands
        for line, index in enumerate(self._visible_log_indices(rows - 1)):
            command = commands[index]
            raw = " ".join(f"{byte:02X}" for byte in command.operands[:4])
            if len(command.operands) > 4:
                raw += " .."
            current = index == self.timeline.index
            marker = ">" if current else " "
            text = (
                f"{marker} {index:6d}  0x{command.offset:06X}  {format_time(command.sample)}  "
                f"{command.opcode:02X} {raw:<12}  {self.descriptions[index]}"
            )
            if current:
                attr = curses.color_pair(PAIR_CURSOR) | curses.A_BOLD
            elif command.opcode == HUC6280_WRITE:
                attr = 0
            else:
                attr = curses.color_pair(PAIR_DIM)
            self._put(screen, top + 1 + line, 0, text, attr)

    def _draw_footer(self, screen, row: int) -> None:
        if self.status:
            self._put(screen, row, 1, self.status, curses.color_pair(PAIR_WARN))
        else:
            self._put(
                screen,
                row,
                1,
                "space play  arrows frame  , . cmd  p n write  l loop"
                "  k keys  t tracker  ? help  q quit",
                curses.color_pair(PAIR_DIM),
            )

    def _draw_help(self, screen) -> None:
        height, width = screen.getmaxyx()
        box_height = len(HELP_LINES) + 4
        box_width = min(width - 2, 56)
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        for line in range(box_height):
            self._put(screen, top + line, left, " " * box_width, curses.A_REVERSE)
        self._put(screen, top + 1, left + 2, "KEYS", curses.A_REVERSE | curses.A_BOLD)
        for line, text in enumerate(HELP_LINES):
            self._put(screen, top + 3 + line, left + 2, text, curses.A_REVERSE)

    def _tracker_mode(self, width: int):
        """None when the tracker does not fit, else True for the wide cell."""
        if width >= TRACKER_WIDE_COLUMNS:
            return True
        if width >= TRACKER_NARROW_COLUMNS:
            return False
        return None

    def _draw_tracker(self, screen, height: int, wide: bool) -> None:
        body = height - 2  # a header line and the footer
        if body <= 0:
            return
        self._put(screen, 0, TRACKER_LEFT, tracker.format_header(wide),
                  curses.A_UNDERLINE | curses.A_BOLD)
        current = tracker.row_at(self.rows, self.timeline.sample)
        first = max(0, min(current - body // 2, len(self.rows) - body))
        for line in range(body):
            index = first + line
            if index >= len(self.rows):
                break
            row = self.rows[index]
            if index == current:
                attr = curses.color_pair(PAIR_CURSOR) | curses.A_BOLD
            elif any(cell.onset for cell in row.cells + row.noise):
                attr = 0
            else:
                attr = curses.color_pair(PAIR_DIM)
            self._put(screen, line + 1, TRACKER_LEFT,
                      tracker.format_row(row, wide), attr)

    def _keyboard_fits(self, height: int) -> bool:
        """The keyboard gives way rather than cut a channel off the table."""
        return height >= HEADER_ROWS + KEYBOARD_ROWS + CHANNEL_ROWS + 1

    def _draw(self, screen) -> None:
        screen.erase()
        height, width = screen.getmaxyx()
        mode = self._tracker_mode(width) if self.show_tracker else None
        self._limit = None if mode is None else TRACKER_LEFT - 1
        row = self._draw_header(screen)
        if self.show_keyboard and self._keyboard_fits(height):
            row = self._draw_keyboard(screen, row)
        row = self._draw_channels(screen, row)
        self._draw_log(screen, row, height - row - 1)
        self._draw_footer(screen, height - 1)
        self._limit = None
        if mode is not None:
            self._draw_tracker(screen, height, mode)
        if self.show_help:
            self._draw_help(screen)
        screen.noutrefresh()
        curses.doupdate()

    # --- input -------------------------------------------------------------

    def _seek(self, sample) -> None:
        self.timeline.seek_sample(sample)
        self.play_position = float(self.timeline.sample)

    def _handle(self, key: int) -> bool:
        """Returns False to quit."""
        self.status = ""
        timeline = self.timeline
        if key == ord("q"):
            return False
        if key == ord(" "):
            self.playing = not self.playing
        elif key == curses.KEY_RIGHT:
            self._seek(timeline.sample + FRAME_SAMPLES)
        elif key == curses.KEY_LEFT:
            self._seek(timeline.sample - FRAME_SAMPLES)
        elif key == ord(">"):
            self._seek(timeline.sample + SAMPLE_RATE)
        elif key == ord("<"):
            self._seek(timeline.sample - SAMPLE_RATE)
        elif key == ord("."):
            timeline.step_command(1)
            self.play_position = float(timeline.sample)
        elif key == ord(","):
            timeline.goto_index(timeline.index - 1)
            self.play_position = float(timeline.sample)
        elif key == ord("n"):
            timeline.step_write(1)
            self.play_position = float(timeline.sample)
        elif key == ord("p"):
            timeline.step_write(-1)
            self.play_position = float(timeline.sample)
        elif key == ord("g"):
            timeline.goto_index(0)
            self.play_position = 0.0
        elif key == ord("G"):
            self._seek(timeline.end_sample)
        elif key == ord("l"):
            loop = timeline.loop_sample
            if loop is None:
                self.status = "this file has no loop point"
            else:
                self._seek(loop)
        elif key == ord("["):
            self.speed_index = max(0, self.speed_index - 1)
        elif key == ord("]"):
            self.speed_index = min(len(SPEEDS) - 1, self.speed_index + 1)
        elif key == ord("k"):
            self.show_keyboard = not self.show_keyboard
        elif key == ord("t"):
            self.show_tracker = not self.show_tracker
        elif key == ord("h"):
            self.writes_only = not self.writes_only
        elif key == ord("?"):
            self.show_help = not self.show_help
        return True

    def _read_key(self, screen) -> int:
        """Read one key. Reassembles arrow keys that arrive as split escapes."""
        key = screen.getch()
        if key != 27:
            return key
        screen.nodelay(True)
        try:
            lead = screen.getch()
            if lead in (ord("["), ord("O")):
                return ARROWS.get(screen.getch(), -1)
            return -1
        finally:
            screen.timeout(POLL_MS)

    def run(self, screen) -> None:
        curses.curs_set(0)
        screen.keypad(True)
        screen.timeout(POLL_MS)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(25)
        _init_colors()
        if self.vgm.warnings:
            self.status = self.vgm.warnings[0]
        last = time.monotonic()
        while True:
            now = time.monotonic()
            elapsed, last = now - last, now
            if self.playing:
                self.play_position += elapsed * SAMPLE_RATE * self.speed
                self.timeline.seek_sample(self.play_position)
                if self.timeline.sample >= self.timeline.end_sample:
                    self.playing = False
                    self.play_position = float(self.timeline.sample)
            self._draw(screen)
            key = self._read_key(screen)
            if key != -1 and not self._handle(key):
                return


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(PAIR_TITLE, curses.COLOR_CYAN, -1)
    curses.init_pair(PAIR_ACTIVE, curses.COLOR_GREEN, -1)
    curses.init_pair(PAIR_DIM, curses.COLOR_BLUE, -1)
    curses.init_pair(PAIR_CURSOR, curses.COLOR_YELLOW, -1)
    curses.init_pair(PAIR_WARN, curses.COLOR_RED, -1)
    curses.init_pair(PAIR_KEY_WHITE, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(PAIR_KEY_BLACK, curses.COLOR_WHITE, curses.COLOR_BLACK)
    for index, color in enumerate(CHANNEL_COLORS):
        curses.init_pair(PAIR_KEY_FIRST + index, curses.COLOR_BLACK, color)
        curses.init_pair(PAIR_CHANNEL_FIRST + index, color, -1)


def run(vgm: VgmFile) -> None:
    locale.setlocale(locale.LC_ALL, "")  # needed for the block characters
    curses.wrapper(Debugger(vgm).run)
