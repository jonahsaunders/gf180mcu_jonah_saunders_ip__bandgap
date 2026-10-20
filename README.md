# GF180MCU Trimmed Bandgap Reference

**Jonah Saunders · Chipalooza #3 · Seven-bit resistor trim**

[Layout](#layout) · [Floorplan](#floorplan) · [Pre-layout](#pre-layout-simulation) · [Post-layout](#post-layout-simulation) · [Schematics](#schematics) · [Simulation setup](#installation-and-first-run)

A bandgap reference targeting **1.18 V** in GF180MCU-D, integrating a folded-cascode error amplifier, bias and startup circuits, a seven-bit resistor-trim network, and MIM capacitors. The complete trim-inclusive core corresponds to **TB10_RESISTOR_TRIM**.

| Technology | Core footprint | Trim | Physical verification |
| :--- | :--- | :--- | :--- |
| GF180MCU-D · 5 metals | 614 × 307.6 µm · 0.189 mm² | 7 bits · 128 codes | 0 reported DRC violations · LVS match |

Physical checks apply to the released core and pinned decks. This is a **layout review candidate**; integration and electrical qualification remain open.

## Layout

[![Annotated GDS layout with numbered analog groups, trim circuitry and capacitor labels](layout/figures/physical-layout.png)](layout/figures/physical-layout.png)

**Fig. 1.** Routed and filled GDS of the complete trim-inclusive core. Twelve numbered callouts identify the analog device groups and startup circuitry; C1–C11 identify the MIM capacitors. The dashed outline marks the deep-n-well trim region. Numbers 1–8 correspond to the matching groups in Fig. 3. This is a layout rendering, not a silicon micrograph. [Vector PDF](layout/figures/physical-layout.pdf)

## Floorplan

The organizer's draft guidance, supplied by the designer on 20 October 2026, allocates approximately **1270 × 410 µm** per user area. The present core occupies **36.3%** of that area. The analog core remains compact, with space available for harness connections, supply routing, isolation, and later matching improvements.

[![Current core centered in the approximate 1270 by 410 micrometer Chipalooza user area](layout/figures/harness-area-fit.png)](layout/figures/harness-area-fit.png)

**Fig. 2.** Geometric fit within the draft user area. Centering leaves 328 µm on each side and 51.2 µm above and below the core. These are geometric margins; final wrapper boundaries, required clearances, slot assignment, and pin locations remain pending. [Vector PDF](layout/figures/harness-area-fit.pdf) · [Announcement and assumptions](layout/planning_envelope.json)

[![Implemented floorplan showing trim logic, poly resistor ladder, matched analog groups and output capacitor bank](layout/figures/implemented-floorplan.png)](layout/figures/implemented-floorplan.png)

**Fig. 3.** Implemented placement, drawn from the saved device coordinates. Trim logic and the resistor ladder occupy the left side; the analog groups lie below the output capacitor bank, with C1 at the right. The numbered legend identifies the matching groups. [Vector PDF](layout/figures/implemented-floorplan.pdf) · [Matching detail](layout/figures/matched-arrays.pdf) · [Historical initial concept](layout/figures/initial-floorplan.pdf)

## Simulation results

### Pre-layout simulation

The original **2k-resistor design** is preserved on [`design_review_1`](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/tree/design_review_1). The temperature-trim chart and specification estimates below belong to that pre-layout build. Its trim codes and results should be read separately from the current 1k-resistor post-layout screens.

#### Temperature-trim study — 28 September 2026

The study covers **45 process combinations** (5 MOS × 3 BJT × 3 resistor, with typical MIM), **−40 to 125 °C**, **3.3 V and 5 V AVDD**, and **3.3 V DVDD**. All 128 codes were first compared at −40, 25, and 125 °C. The best sampled code and its two neighbors were then checked at 1 °C steps: **135/135 candidate jobs completed**, producing **44,820 DC sample points**. One selected code is held across both supplies and all temperatures for each process combination. All **14,940 selected-code operating points** meet the existing self-sustaining DC current checks.

[![Pre-layout temperature drift and output voltage after calibration for minimum drift](docs/results/trim-temperature-2026-09-28/temperature_drift_after_trim.png)](docs/results/trim-temperature-2026-09-28/temperature_drift_after_trim.png)

**Fig. 4.** Original pre-layout temperature-trim results. The left chart shows drift relative to each curve's own 25 °C output; solid and dashed curves indicate 3.3 V and 5 V supplies. Each point in the right chart represents one process combination and its worst temperature coefficient across the two supplies. Typical MIM is used; mismatch and layout parasitics are excluded.

| Pre-layout result after minimum-drift calibration | Value |
| :--- | ---: |
| Typical output, 25 °C / 3.3 V | 1.18425 V |
| Output across selected temperature curves and both supplies | 1.173776–1.189448 V |
| Box temperature coefficient: best / typical / median / worst | 0.49 / 2.54 / 2.99 / 5.28 ppm/°C |
| Selected trim codes | 65–98 |

The process corner previously giving 65.5 ppm/°C when calibrated toward 1.200 V gives 3.28 ppm/°C with a different code. That earlier figure used three temperatures; this study uses the dense sweep.

The calibration objective is **minimum temperature drift**, with an output near **1.18 V**. The standard TB10 runner instead selects a code for **1.194 V at 25 °C**; it does not automatically reproduce this separate minimum-drift study. These deterministic DC results do not establish post-trim mismatch yield, startup/restart, PSRR, noise, or stability at the selected codes.

[Study details and method](docs/results/trim-temperature-2026-09-28/README.md) · [Selected codes and metrics (CSV)](docs/results/trim-temperature-2026-09-28/selected_codes.csv) · [Temperature curves (CSV)](docs/results/trim-temperature-2026-09-28/selected_curves.csv) · [Full TB01–TB10 simulation review](docs/RESULTS.md)

<a id="specifications"></a>
<a id="projected-post-trim-specifications"></a>

#### Pre-layout specification estimates

Conservative planning estimates for the resistor-trim core, with a **1.18 V nominal goal**. Select the trim code for minimum temperature drift, then hold it fixed across supply and temperature. Min / Typical / Max entries are estimated values and design budgets, not measured or guaranteed limits.

| Parameter | Min | Typical | Max | Unit |
| --- | ---: | ---: | ---: | --- |
| Analog supply voltage | 3.3 | 3.3 | 5.0 | V |
| Trim logic supply voltage (fixed) | 3.3 | 3.3 | 3.3 | V |
| Operating temperature | −40 | 25 | 125 | °C |
| Output voltage after trim (1.18 V nominal) | 1.1682 | **1.184** | 1.1918 | V |
| Analog supply current | 15 | 30 | 60 | µA |
| Load regulation† | 0.1 | 0.5 | 1 | % |
| Line regulation, 3.3-5.0 V | 0.01 | 0.02 | 0.1 | % |
| Output noise density at 1 kHz | 900 | 1,500 | 3,000 | nV/√Hz |
| Integrated noise, 0.1-10 Hz | 50 | 70 | 100 | µV RMS |
| Initial output error relative to 1.18 V | −1 | +0.36 | +1 | % |
| PSRR at 1 kHz | 55 | 75 | 85 | dB |
| PSRR throughout 1 Hz-1 MHz | 45 | 50 | 55 | dB |
| Temperature coefficient after trim | 0.5 | 3 | **10** | ppm/°C |
| Startup / restart after supply ramp† | 75 | 200 | 300 | µs |
| PTAT output current† | 180 | 220 | 260 | nA |
| Trim resolution (fixed) | 7 | 7 | 7 | bits |

Typical values are rounded estimates near nominal conditions; the 1.184 V estimate reflects calibration for minimum drift rather than exact voltage. †Load regulation, PTAT current, and startup/restart at the new codes are provisional guesses. See **[Results](docs/RESULTS.md#basis-for-the-specification-estimates)** for the estimate basis and historical simulation evidence.

### Post-layout simulation

Current **post-layout simulation** results use typical device corners, trim code 72, DVDD = 3.3 V, and 25 °C unless stated otherwise. The extracted model uses a capacitance-conserving RCC approximation; ground resistance is omitted, and distributed coupling placement remains approximate. These are simulated screening results.

| Parameter | AVDD = 3.3 V | AVDD = 5.0 V | Unit |
| :--- | ---: | ---: | :--- |
| Reference output | 1.194404 | 1.194579 | V |
| Analog supply current | 24.33 | 31.67 | µA |
| Box temperature coefficient, −40 to 125 °C | 10.08 | 10.01 | ppm/°C |
| Minimum PSRR, 1 Hz to 1 MHz | 47.20 | 52.16 | dB |
| Cold-start settling, both rails cycled | 200.5 | 110.6 | µs |
| Restart settling, both rails cycled | 109.3 | 47.4 | µs |

Settling is measured to ±0.5% of the final value after a 100 µs supply ramp, with high trim inputs following DVDD and no external output load. Code 72 is fixed for these screens; it is not a completed minimum-drift calibration. [Nominal data](layout/release/nominal_screen.json) · [PSRR data](layout/release/psrr/summary.json) · [Supply-sequence data](layout/release/power_sequence_screen.json)

[![Schematic and extracted PSRR comparisons at 3.3 and 5 volt analog supplies](layout/figures/psrr-before-after.png)](layout/figures/psrr-before-after.png)

**Fig. 5.** PSRR comparison at both analog supply voltages. Correcting the extraction accounts for most of the apparent recovery; the physical routing revision improves the minimum by approximately 0.24–0.25 dB. The remaining 3.3 V gap to the schematic minimum is 3.36 dB. [Vector PDF](layout/figures/psrr-before-after.pdf) · [Comparison data](layout/release/psrr/comparison.json)

**Qualification status.** The nominal screens exceed the 45 dB planning floor, but neither the schematic nor the layout meets the existing 60 dB testbench PSRR criterion. At code 72, the output exceeds the 1.18 V ±1% budget and the temperature coefficient is slightly above 10 ppm/°C. Final harness integration, complete ground-network extraction, matching and route review, EM/IR, and full analog qualification remain open. See the [reference design budgets](docs/REFERENCE_SPECIFICATIONS.md), [release evidence](layout/release/verification.json), and [detailed review report](layout/report.html).

## Schematics

| Circuit | Schematic image | Xschem source |
| :--- | :--- | :--- |
| Trim-inclusive core used for layout | [Full-size SVG](docs/images/bandgap-core-resistor-trim.svg) | [Bandgap_Core_Res.sch](Bandgap_Core_Res.sch) |
| Untrimmed reference core | [Full-size SVG](docs/images/bandgap-core.svg) | [Bandgap_Core.sch](Bandgap_Core.sch) |
| Seven-bit resistor-trim testbench | [Testbench guide](docs/TESTBENCHES.md) | [TB10_RESISTOR_TRIM.sch](TB10_RESISTOR_TRIM.sch) |

<details>
<summary>View the trim-inclusive schematic</summary>

[![Bandgap core with physical seven-bit resistor trim](docs/images/bandgap-core-resistor-trim.svg)](docs/images/bandgap-core-resistor-trim.svg)

</details>

## Installation and first run

### 1. Open the simulation environment

Use **Bash inside Linux/FOSS**, with Python 3.9+, Xschem, ngspice, and the GF180 symbol/model libraries installed. The runner uses POSIX file locks and does not run directly in Windows PowerShell. The PDK is installed separately.

In IIC-OSIC-TOOLS, use the full PDK selector in the same terminal before opening Xschem:

```bash
source /foss/tools/sak/sak-pdk-script.sh gf180mcuD
command -v python3 xschem ngspice
```

This sets `PDK`, `PDKPATH`, and ngspice startup settings together. The interactive alias is `sak-pdk gf180mcuD`; sourcing its script also works in Bash scripts. Repeat the selection in each new terminal.

The testbench model paths assume `/foss/pdks/gf180mcuD/libs.tech/ngspice/`. If your PDK is elsewhere, update each bench's `MODELS` paths before netlisting.

### 2. Get the project and Python dependencies

```bash
cd /foss/designs
git clone https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap.git
cd gf180mcu_jonah_saunders_ip__bandgap

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 verify_suite.py
```

For an existing checkout, start in its repository folder. When reviewing an unmerged branch, switch to that branch before installation. Keep the core `.sch`/`.sym` files, benches, scripts, and `bandgap_config.json` together. The virtual environment is optional if NumPy and Matplotlib are already installed. Software checks do not simulate the circuit.

### 3. Configure the Xschem launchers

Close open testbenches, then run:

```bash
NETLIST_DIR="/headless/.xschem/simulations"
mkdir -p "$NETLIST_DIR"
python3 run_bandgap.py --configure --netlist-dir "$NETLIST_DIR"
```

Use your actual Xschem netlist directory. Reopen the benches afterward. Repeat this step after moving the repository; it updates launcher paths, not core circuitry or PDK model paths.

### 4. Run a smoke check

Generate TB01's netlist, run its short profile, and plot the results:

```bash
source /foss/tools/sak/sak-pdk-script.sh gf180mcuD
NETLIST_DIR="${NETLIST_DIR:-/headless/.xschem/simulations}"
mkdir -p "$NETLIST_DIR"
xschem -n -x -q -r -s -o "$NETLIST_DIR" \
  -N TB01_PSRR.spice TB01_PSRR.sch

python3 run_bandgap.py --test TB01_PSRR \
  --deck "$NETLIST_DIR/TB01_PSRR.spice" --profile smoke

python3 plot_bandgap.py --profile smoke --test TB01_PSRR
```

Check for missing-symbol or netlisting errors before running the Python command. The report is `results/smoke/TB01_PSRR_UPLOAD_THIS.txt`; plots are in `results/smoke/plots/`. Regenerate the matching netlist whenever its schematic changes.

For GUI runs, select GF180 and open a bench from that same terminal. Relaunch any Xschem window that previously loaded another PDK. Then click **Netlist**, then **Simulate**. Each schematic runs only its own bench. GUI profiles come from `bandgap_config.json`; the supplied defaults select **full**, including TB10. A terminal `--profile smoke` command does not change those defaults.

For the full-suite command, profile settings, troubleshooting, and **rerunning selected cases without replacing old results**, use the [simulation guide](docs/SIMULATION_GUIDE.md).

## Documentation

| Resource | Contents |
| :--- | :--- |
| [Layout reproduction](layout/README.txt) | Pinned tools, build flow, extraction assumptions, and open work |
| [Release verification](layout/release/verification.json) | DRC/LVS results, tool versions, and source/artifact hashes |
| [Figure exports](layout/figures) | Nine figures in PDF, SVG, and 600-dpi PNG; captions included |
| [Testbench reference](docs/TESTBENCHES.md) | TB01–TB10 and measurement coverage |
| [Software validation](VALIDATION.md) | Runner, parser, and plotting checks |
| [Historical simulation results](docs/RESULTS.md) | Results from the original 2k-resistor build |

The current `main` branch uses the PDK's `ppolyf_u_1k` model, with resistor lengths doubled to preserve nominal resistance. The original build is retained on [`design_review_1`](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/tree/design_review_1); the [design-review notes](docs/DESIGN_REVIEW.md) explain the conversion and Python readability changes.
