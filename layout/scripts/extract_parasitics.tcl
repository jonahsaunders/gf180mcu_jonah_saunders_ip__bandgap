drc off
crashbackups stop
locking disable
gds readonly false
gds read [file join $::env(BANDGAP_LAYOUT_BUILD) pex extraction_view.gds]
source [file join $::env(BANDGAP_LAYOUT_BUILD) pex define_primitives.tcl]
load Bandgap_Core_Res
select top cell
extract do capacitance
extract do coupling
extract no resistance
extract all
ext2spice lvs
ext2spice -o Bandgap_Core_Res_magic_lvs.spice
ext2spice cthresh 0.1
ext2spice rthresh infinite
ext2spice resistor tee off
ext2spice merge conservative
ext2spice -o Bandgap_Core_Res_c.spice
extresist threshold 1000
extresist minres 100
extresist mindelay 0
# Preserve separate ground-metal networks. Magic otherwise walks through
# space/well planes and creates a spurious AVSS-to-DVSS resistive path.
# Wells/substrate remain lumped; metal, contacts and vias are distributed.
# Each type must be a separate command in Magic 8.3.685.
extresist skip space
extresist skip pwell
extresist skip nwell
extresist all
ext2spice extresist on
ext2spice -o Bandgap_Core_Res_rc.spice
quit -noprompt
