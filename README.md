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
pcevgm file.vgm --info        # header, clocks, GD3 tags
pcevgm file.vgm --dump 200    # first 200 decoded commands
```

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
| `src/pcevgm/tui.py` | curses screen and key handling |
| `src/pcevgm/cli.py` | argument parsing, `--info` and `--dump` |
| `tools/make_example.py` | builds a synthetic test file |

## Known limits

- No audio output.
- Seeking backwards replays the stream from the start.
- The volume figure uses 1.5 dB per amplitude step and 3.0 dB per balance step.
  It is a model of the hardware curve, not a measurement.
- Noise frequency shows the raw 5-bit register, not a rate in Hz.
- The LFO registers are stored and shown but not applied to channel pitch.
- Commands for other chips are decoded for length and time only.
