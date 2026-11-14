# GF180MCU bandgap reference

This is a bandgap reference for the GlobalFoundries GF180MCU-D process,
designed for Chipalooza #3. It uses a folded-cascode error amplifier,
a startup circuit, and seven-bit resistor trim, with a nominal output
target of 1.18 V.

[![3D view of the bandgap layout](docs/images/bandgap-design.png)](docs/images/bandgap-design.png)

- [Design documentation](docs/DESIGN.md)
- [Final Integration](docs/FINAL_INTEGRATION.md)
- [Installation and first run](docs/GETTING_STARTED.md)
- [Testbench reference](docs/TESTBENCHES.md)

## Schematics

The Xschem schematics and symbols are in the repository root.
`Bandgap_Core_Res.sch` is the trimmed core, including the dummy devices
used for LVS. `TB10_RESISTOR_TRIM.sch` is its testbench.
`Bandgap_Core.sch` is the untrimmed version.

The [schematic drawings](docs/SCHEMATICS.md) show the functional circuit
and the complete sheet.

## Layout

The layout is in `layout/`. The released GDS is
[`Bandgap_Core_Res.gds`](layout/release/Bandgap_Core_Res.gds);
the core measures 682.8 × 396.76 µm. Build instructions and tool versions
are in [layout/README.txt](layout/README.txt).

The released core has zero reported DRC violations with the pinned deck,
and LVS matches all 533 device units. The slot-4 layout,
[`gds/slot_4.gds.gz`](gds/slot_4.gds.gz), also passes DRC and LVS on its own;
the harness top level has not been run. See
[slot 4 integration](docs/SLOT4_INTEGRATION.md).

### Chipalooza harness integration

Tim Edwards assigned the bandgap reference to **slot 4** of the GF180MCU
harness for the planned December 2026 tapeout.

- Match the layout coordinates in
  [`slot4_wrapper.gds`](https://github.com/RTimothyEdwards/gf180mcu_ocd_chipalooza/blob/main/gds/slot4_wrapper.gds).
  You can copy the wrapper cell or its contents into the slot layout.
- Submit `slot_4.gds` or `slot_4.gds.gz` with `slot_4` as the top-level cell.
- The default pad is a 5 V analog pad from the dual-voltage I/O library
  (3.3 V core, 5 V periphery). Pads can be configured individually. If a
  pad needs a different cell, such as a digital input or output cell,
  send the cell name to Tim Edwards for the harness
  [`config.txt`](https://github.com/RTimothyEdwards/gf180mcu_ocd_chipalooza/blob/main/config.txt).

The slot layout is [`gds/slot_4.gds.gz`](gds/slot_4.gds.gz), top cell `slot_4`:
vref on `s4_an[0]`, AVDD/AVSS on the 5 V pins, DVDD/DVSS on the 3.3 V pins and
trim bits b0–b6 on `dig_in[0..6]`. Both dedicated pads keep the default
analog pad type. Placement, wiring, verification and the open questions for
the harness are in [slot 4 integration](docs/SLOT4_INTEGRATION.md).

Final design reviews are planned for the week of **November 30, 2026**
to confirm that the slot layout integrates into the harness top level.

<a id="installation-and-first-run"></a>

## Simulation

The testbenches use ngspice, with Python scripts to run sweeps and plot
results. Use Bash in Linux/FOSS with Python 3.9+, Xschem, ngspice, and
the GF180MCU-D PDK installed.

After completing the setup instructions, netlist `TB01_PSRR.sch` in
Xschem. With the netlist in `/headless/.xschem/simulations/`, run
these commands from the repository root:

```bash
python3 run_bandgap.py --test TB01_PSRR \
  --deck /headless/.xschem/simulations/TB01_PSRR.spice --profile smoke
python3 plot_bandgap.py --profile smoke --test TB01_PSRR
```

Change the netlist path if needed. See the [simulation guide](docs/SIMULATION_GUIDE.md)
for other benches, full runs, and reruns.

<a id="simulation-results"></a>

## Results

[Current post-layout results](docs/POWER_RAIL_REVIEW.md) compare freshly
rebuilt layouts using fixed trim code 72 and an approximate RCC extraction.
The new rails substantially reduce most supply-path resistances; four nominal
startup/restart cases and all 360 fixed-code DC corner points complete.
PSRR, supply-current and noise regressions are reported explicitly; the
exploratory load sweep also exposes an existing output-loading limitation. Full PVT
startup, mismatch yield, and electrical qualification remain open; PSRR is
still below the testbench's 60 dB target.

The pre-power-rail `main` is preserved on
[`design_review_2`](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/tree/design_review_2).
The 3D preview and GLB use the released GDS polygons; layer heights are
illustrative and are not fabrication dimensions.

<a id="specifications"></a>
<a id="projected-post-trim-specifications"></a>

The current design uses 1k poly resistors. The
[pre-layout results and estimates](docs/DESIGN.md#pre-layout-simulation)
are for the older 2k design, preserved on
[`design_review_1`](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/tree/design_review_1).

Designed by Jonah Saunders.

