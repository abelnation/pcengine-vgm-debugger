# pcevgm

A terminal debugger for PC Engine (HuC6280) VGM music files.

It parses a `.vgm` or `.vgz` file, replays the command stream into HuC6280
register state, and shows that state as you move through the song. It does not
produce audio.

## Requirements

Python 3.10 or later. No third-party packages. The user interface uses the
`curses` module from the standard library.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

```sh
python tools/make_example.py      # writes examples/scale.vgm
pcevgm examples/scale.vgm
```

Without installing:

```sh
PYTHONPATH=src python3 -m pcevgm examples/scale.vgm
```

Text modes:

```sh
pcevgm file.vgm --info             # header, clocks, GD3 tags
pcevgm file.vgm --dump 200         # first 200 decoded commands
pcevgm file.vgm --extract-waves    # write the wave tables to file.vgm.wavs/
```

## Wave extraction

`--extract-waves` replays the stream and keeps every wave table the track
uploads. A table is 32 samples of 5 bits. The extractor captures one each time
a channel finishes a full pass of 32 writes to the wave data register. Identical
tables collapse into one entry.

Output goes to the input path plus `.wavs`, so `song.vgz` gives `song.vgz.wavs/`.
Use `--out DIR` to write somewhere else.

```
song.vgz.wavs/
  manifest.txt       where each wave is uploaded, and an ASCII plot
  wave-00.pcm        32 raw bytes, one per sample, values 0 to 31
  wave-00.hex        the same bytes as text, 16 to a line
  wave-00.wav        one cycle, uncompressed 16-bit mono at 44100 Hz
  wave-00.long.wav   the same cycle repeated, to preview by ear
  wave-01.pcm
  ...
```

`wave-NN.wav` holds a single cycle, so it lasts 0.7 ms. Load it in a sampler
and loop it. `wave-NN.long.wav` repeats that cycle at 440 Hz for 2 seconds, so
any audio player gives you the timbre. Change it with `--preview-hz` and
`--preview-seconds`.

Both files scale the 5-bit samples around their 15.5 midpoint, so a flat table
is silence and the range 0 to 31 fills 16-bit full scale. The preview picks the
nearest wave sample for each output frame and does not interpolate, because the
chip holds each sample for a fixed time. It fades 5 ms at each end.

The command never deletes files. It names any leftover `wave-*` file from an
earlier run so you can remove them yourself.

## Screen

- Header: file name, VGM version, chip clocks, GD3 tags, transport, progress.
- Keyboard: five octaves, C1 to B5, showing what sounds right now. Each layer
  of keys is two character rows tall. A white key is two cells wide on the
  lower layer, so it holds four cells. A black key is one cell wide on the
  upper layer, over the right half of the white key below it, so it holds two.
  An active key changes background colour, one colour per channel, so the key
  keeps its shape. The digit names the channel.
  - The cells of a key are shared out between the channels sounding it. One
    channel fills the key. Two split it in half, one above the other. Four take
    a cell each. Beyond that the last cell shows `+`.
  - A pitch off the ends of the board marks the edge with `<` or `>`.
  - A channel with noise enabled has no pitch, so the board leaves it out
    entirely. The channel table reports its noise register. A channel in
    DDA mode is named beside the board instead.
  - Press `k` to hide it. It hides itself below 29 terminal rows rather than
    push a channel off the table.
- Channel table: two rows per PSG channel.
  - First row: `ST` (`ON`, `DDA` or `off`), `DIV` (the 12-bit frequency
    divider), `HZ` and `NOTE` (the divider as a pitch, with cents offset),
    `dB L` and `dB R` (attenuation per side), `AMP` and `BAL` (the raw
    amplitude and balance registers).
  - Second row: the name of the wave table the channel holds, the DDA sample
    while in DDA mode and the noise register on channels 4 and 5, then a
    horizontal meter under each
    of `dB L`, `dB R` and `AMP`. The level meters read empty at -60 dB and full
    at 0 dB. The `AMP` meter tracks the raw 0 to 31 register. Partial blocks
    give each meter character 8 steps.
  - `WAVE` — the 32-entry wave table, plotted two character rows tall. Two rows
    give 16 levels instead of the 8 a single row can show.
- Master line: the master balance, the selected channel and the write count.
- LFO line: register 9 split into its two fields. `enabled` is bit 7, which
  disables the LFO and resets its source channel when set. `depth` is bits 1
  and 0, which scale the source channel output by 0, 1, 16 or 256 before it
  reaches the pitch. The raw register byte stays on the line. The line dims
  unless the LFO is enabled at a non-zero depth.
- Log: the command stream around the cursor. The current command is marked `>`.

The wave name on a channel's second row is the file stem `--extract-waves`
writes, so `wave-03` on channel 2 means that channel holds the table in
`wave-03.pcm`. Run the dump once and you can hear any channel's timbre through
`wave-03.long.wav`. A channel reads `wave  --` until its table matches a
complete upload, which includes the start of every track.

## Keys

| Key | Action |
| --- | --- |
| `space` | play / pause |
| `left` `right` | step one video frame (735 samples) |
| `<` `>` | step one second |
| `,` `.` | step one command |
| `p` `n` | step one HuC6280 register write |
| `g` `G` | go to start / end |
| `l` | go to the loop point |
| `[` `]` | slower / faster |
| `k` | show or hide the keyboard |
| `h` | log filter: all commands or HuC6280 writes only |
| `?` | show or hide the key list |
| `q` | quit |

## Tests

```sh
python -m unittest discover -s tests -t .
```

## Layout

| Path | Contents |
| --- | --- |
| `src/pcevgm/vgm.py` | file, header, GD3 and command stream parsing |
| `src/pcevgm/huc6280.py` | PSG register model and pitch / level maths |
| `src/pcevgm/player.py` | cursor over the stream, state replay, command text |
| `src/pcevgm/waves.py` | wave table extraction and the `.pcm` / `.hex` output |
| `src/pcevgm/keyboard.py` | the piano keyboard view of the current pitches |
| `src/pcevgm/tui.py` | curses screen and key handling |
| `src/pcevgm/cli.py` | argument parsing and the text modes |
| `tools/make_example.py` | builds a synthetic test file |

## Known limits

- No audio output.
- Seeking backwards replays the stream from the start.
- The volume figure uses 1.5 dB per amplitude step and 3.0 dB per balance step.
  It is a model of the hardware curve, not a measurement.
- Noise frequency shows the raw 5-bit register, not a rate in Hz.
- The LFO registers are stored and shown but not applied to channel pitch.
- The keyboard shows instantaneous pitch with no smoothing, so vibrato makes a
  key marker jump between neighbours. It rounds to the nearest semitone; the
  `NOTE` column carries the cents offset.
- A channel counts as sounding on the keyboard above -40 dB. At -60 dB the
  board fills with channels parked on divider 0 at 27 Hz.
- The keyboard needs about 74 terminal columns and 29 rows.
- Commands for other chips are decoded for length and time only.
- Wave extraction ignores a partial pass. A track that rewrites only part of
  a table produces no new entry until the next full pass.
