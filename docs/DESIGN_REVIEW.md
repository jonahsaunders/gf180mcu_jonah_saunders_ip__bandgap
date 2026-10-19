# Design review changes — 2026-10-19

The original build is preserved on [`design_review_1`](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/tree/design_review_1), at commit `28e3c1114d4984cd8f6888d6509e786220378dfc`. The changes below are applied to `main`.

## Resistor option and dimensions

The review clarification selects **`ppolyf_u_1k` in place of `ppolyf_u_2k`**. These names describe the sheet-resistance option, not the total resistance of an instance. The public PDK contains several models; this project uses the 1k process option requested for this build. The [open_pdks GF180 technology file](https://github.com/fossi-foundation/open-pdks/blob/main/gf180mcu/magic/gf180mcu.tech) also describes its default high-resistance-poly option as 1 kΩ/square.

All 16 instances in `Bandgap_Core.sch`, `Bandgap_Core_LoopProbe.sch`, and `Bandgap_Core_Res.sch` now use the 1k symbol and model. Lengths double, while widths, multiplicities, wiring, and trim-bit assignments stay the same. Using the nominal sheet formula, `R = R_sheet × L / W`, gives:

| Instance | Width (µm) | Old length, 2k (µm) | New length, 1k (µm) | Nominal resistance |
| --- | ---: | ---: | ---: | ---: |
| R1, all three cores | 4 | 21.05 | 42.10 | 10.525 kΩ |
| R2, untrimmed and loop-probe cores | 4 | 192.5 | 385.0 | 96.250 kΩ |
| RFIXED, RFIXED1–3, each | 6.6 | 64 | 128 | 19.394 kΩ |
| RB0–RB6 | 6.6 | 1, 2, 4, 8, 16, 32, 64 | 2, 4, 8, 16, 32, 64, 128 | 303.030 Ω × 2^bit |

This preserves nominal resistance, ratios and binary weighting. It does not equate the two models' end corrections, temperature coefficients, mismatch, noise or parasitics. The larger resistor bodies also affect layout area. Recheck layout constraints, trim calibration and electrical performance with the selected PDK. Historical plots and specification estimates remain labeled as evidence from the original 2k build.

## Reviewing the Python

The runner remains a standard-library-only script, with its existing command-line interface and report format. It has normal indentation, one statement per line, expanded expressions, descriptive orchestration variables and docstrings. The active plotting, rerun and verification scripts use the same formatting.

The large Base85/zlib predecessor-source payload and its legacy cache-migration path have been removed. The payload was previously decompressed for hashing, not executed. Its removal makes the source inspectable without decoding a second program. Current-version resume still works, but only when the complete fingerprint matches. Older caches must be rerun; their reports can still be plotted. Use a new `--results-root` after this update to preserve earlier results.

Repeated definitions and `_previous_*` aliases have been replaced by explicitly named helpers. Each function now has one definition. The measurement computations are unchanged; the deliberate runtime change is removal of predecessor-cache migration.

Start code review with these entry points in [`run_bandgap.py`](../run_bandgap.py):

| Function | Responsibility |
| --- | --- |
| `parse_arguments`, `main` | CLI options, preflight checks, scheduling and report output |
| `load_config`, `validate_config`, `cases` | Configuration precedence, validation and PVT case selection |
| `prepare_source`, `model_dependencies` | Netlist checks and recursive model-file discovery |
| `source_for_case`, `control_for_case`, `prepare_case_deck` | Plain-text SPICE generation |
| `run_case`, `run_trim_case`, `trim_simulate` | Simulator invocation, per-case files and resume behavior |
| `read_result`, `summarize`, `trim_*` metric helpers | Measurement parsing and pass/fail calculations |
| `run_fingerprint` | SHA-256 inputs used to decide whether a cache is reusable |
| `configure` | The explicit `--configure` operation that rewrites testbench launchers |

`subprocess.run` invokes ngspice with an argument list. Compressed waveform files remain because they contain simulation data used by the plotter. Readability makes code review easier; it is not a security certification.

## Maintaining and verifying the changes

In Linux/FOSS, run the software and schematic checks:

```bash
python3 verify_suite.py
python3 verify_plotting.py
source /foss/tools/sak/sak-pdk-script.sh gf180mcuD
python3 verify_xschem_symbols.py
```

Formatting is configured in `pyproject.toml`. In a development environment with Python 3.10 or newer:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m black --check .
```

The runner still supports Python 3.9+. See [validation notes](../VALIDATION.md) for the actual checks performed and remaining electrical limits. The [simulation guide](SIMULATION_GUIDE.md) explains fresh output directories and complete reruns.
