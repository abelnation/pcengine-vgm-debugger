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
  manifest.txt     where each wave is uploaded, and an ASCII plot
  wave-00.pcm      32 raw bytes, one per sample, values 0 to 31
  wave-00.hex      the same bytes as text, 16 to a line
  wave-01.pcm
  wave-01.hex
```

The command never deletes files. It names any `wave-*.pcm` or `wave-*.hex` left
over from an earlier run so you can remove them yourself.

## Screen

- Header: file name, VGM version, chip clocks, GD3 tags, transport, progress.
- Channel table: one row per PSG channel.
  - `ST` — `ON`, `DDA`, or `off`.
  - `DIV` — the 12-bit frequency divider.
  - `HZ` / `NOTE` — divider turned into a pitch. `NOTE` shows cents offset.
  - `dB L` / `dB R` — attenuation per side.
  - `AMP` / `BAL` — the raw amplitude and balance registers.
  - `WAVE` — the 32-entry wave table as a sparkline.
- Log: the command stream around the cursor. The current command is marked `>`.

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
- Commands for other chips are decoded for length and time only.
- Wave extraction ignores a partial pass. A track that rewrites only part of
  a table produces no new entry until the next full pass.
