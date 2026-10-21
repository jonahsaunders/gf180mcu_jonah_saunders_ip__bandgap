# Native dummy symbols — simulation confirmation

**Historical PASS · previous GDS at `9b2f539` · 21 October 2026.** Eight fresh ngspice runs confirm that replacing the dummy code block with native Xschem transistor symbols leaves nominal mirror matching and PSRR unchanged. All compared values are identical at the saved simulator precision. Previous simulation inputs, results and plots remain preserved.

TT process, 25 °C, trim code 72, DVDD = 3.3 V, AVDD = 3.3/5 V, no external output load. Each PSRR sweep contains 481 frequencies from 1 Hz to 1 MHz. The fresh source netlist contains all 13 dummy instances (261 device units); no dummy is removed or bypassed.

## PSRR

| Model | AVDD (V) | Previous minimum (dB) | Fresh minimum (dB) | Maximum curve change (dB) |
| :--- | ---: | ---: | ---: | ---: |
| Schematic | 3.3 | 50.55737492 | 50.55737492 | 0 |
| Schematic | 5 | 52.03993267 | 52.03993267 | 0 |
| Extracted RCC | 3.3 | 48.10031619 | 48.10031619 | 0 |
| Extracted RCC | 5 | 53.36893811 | 53.36893811 | 0 |

The comparison checks every frequency sample, not only the minimum. VREF and total analog supply current are also unchanged at saved precision.

## Nominal mirror matching

| Current ratio error | Schematic, 3.3 V (%) | Schematic, 5 V (%) | RCC, 3.3 V (%) | RCC, 5 V (%) |
| :--- | ---: | ---: | ---: | ---: |
| M1/M2 | -0.00477890 | -0.01330077 | -0.00438874 | -0.01297449 |
| M5/M2 | +0.00393692 | +0.00260637 | +0.00425437 | +0.00284428 |
| PLOAD2/PLOAD1 | +0.00067052 | +0.00209063 | -0.00993812 | -0.01170821 |
| MSINK2/MSINK1 | +0.00193313 | +0.00034763 | +0.00226211 | +0.00077552 |
| MFOLD2/MFOLD1 | +0.00118908 | +0.00364716 | -0.00940853 | -0.00996869 |
| PRMIR/PREF | +1.38109748 | +2.59456713 | +1.38278231 | +2.59685420 |
| M18/M16 | +4.03838908 | +9.72315428 | +4.02862064 | +9.71244164 |

Error is `100 × (I_first / I_second − 1)`. All seven ratios have **zero change** from the archived results at both supplies and in both models. The comparison also checks currents and terminal biases for every one of the 104 named MOS instances, including all 13 dummy banks. The extracted diagnostic uses 439 drain probes, aggregated by original schematic instance. Every compared current and bias is unchanged.

This confirms nominal DC behavior; random mismatch yield was not rerun. The existing schematic/layout PSRR difference remains unchanged, as expected for this schematic representation change.

## Method and evidence

1. Verify hashes of the prior release, then freshly netlist `TB10_RESISTOR_TRIM.sch` with Xschem. Compare device models, parameters, nodes and multiplicities with the archived code-block circuit; the netlists agree after removing comments, continuation formatting and statement ordering.
2. Run four unprobed OP/AC cases using the fresh schematic and unchanged released RCC extraction. Use the original model header, KLU solver and 80 points/decade. The extracted runs are a simulator/extraction control; the physical GDS has not changed.
3. Run four DC mirror cases with ideal zero-volt drain probes. Check their VREF against the fresh unprobed operating points, then compare all device currents, biases and seven review ratios with the prior results.
4. Compare all four PSRR curves and fail if any fixed tolerance is exceeded: 0.000001 dB for PSRR, 1 pA for branch current, 1 µV for voltage, or 0.0001 percentage point (1 ppm) for a current ratio. All measured differences are zero. Output folders must be empty, so cached simulation results cannot satisfy this check.

[Machine-readable comparison](comparison.json) · [Fresh mirror results](mirror_bias/summary.json) · [Fresh PSRR results](psrr/summary.json) · [Fresh full TB10 netlist](netlists/TB10_RESISTOR_TRIM.spice) · [Exact schematic snapshot](source/Bandgap_Core_Res.sch) · [Run file hashes](file_hashes.json)

Each simulation directory retains the input deck, DUT netlist, ngspice log and results. The original baselines remain in [mirror_bias](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/blob/9b2f5390b33c18b2c4f167ed6d3468e5d9a72f22/layout/release/mirror_bias/summary.json) and [psrr](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/blob/9b2f5390b33c18b2c4f167ed6d3468e5d9a72f22/layout/release/psrr/summary.json). [Native-symbol LVS](../schematic-symbols/verification.json) remains valid for this exact source and unchanged GDS; this rerun does not rebuild the layout or replace those physical checks.

## Reproduce

Check out commit `9b2f539` to reproduce this historical comparison. Use the pinned container and GF180MCU-D environment in [layout/README.txt](https://github.com/jonahsaunders/gf180mcu_jonah_saunders_ip__bandgap/blob/9b2f5390b33c18b2c4f167ed6d3468e5d9a72f22/layout/README.txt). From the repository root inside that environment:

```sh
source /foss/tools/sak/sak-pdk-script.sh gf180mcuD
python layout/scripts/confirm_symbol_simulations.py --out layout/build/symbol-rerun
```

Choose a new empty output directory for each rerun. The script does not modify the archived release.
