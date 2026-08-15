# Portable Tcl generated from a recorded GoalEvolve_v2 parent flow.
foreach required {GOALEVOLVE_BENCHMARK_ROOT GOALEVOLVE_PROJECT_ROOT GOALEVOLVE_AE_OUTPUT} {
  if {![info exists ::env($required)] || $::env($required) eq ""} {
    error "artifact evaluation requires environment variable $required"
  }
}
proc goalevolve_path {recorded_path} {
  set substitutions [list \
    "__BENCHMARK_ROOT__" $::env(GOALEVOLVE_BENCHMARK_ROOT) \
    "__PROJECT_VENDOR__/mlcad2026_official" [file join $::env(GOALEVOLVE_PROJECT_ROOT) third_party official_checker] \
    "__PROJECT_VENDOR__" [file join $::env(GOALEVOLVE_PROJECT_ROOT) third_party] \
    "__OUTPUT_ROOT__" $::env(GOALEVOLVE_AE_OUTPUT)]
  return [string map $substitutions $recorded_path]
}

set start [clock seconds]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/asap7_tech_1x_201209.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/asap7sc7p5t_28_L_1x_220121a.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/asap7sc7p5t_28_SL_1x_220121a.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/asap7sc7p5t_28_SRAM_1x_220121a.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/sram_asap7_16x256_1rw.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/sram_asap7_256x128_1rw.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/sram_asap7_32x128_1rw.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/sram_asap7_32x256_1rw.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/sram_asap7_64x256_1rw.lef}]
read_lef [goalevolve_path {__BENCHMARK_ROOT__/asap7/lef/sram_asap7_64x64_1rw.lef}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_AO_LVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_AO_RVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_AO_SLVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_INVBUF_LVT_FF_nldm_220122.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_INVBUF_SLVT_FF_nldm_220122.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_OA_LVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_OA_RVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_OA_SLVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_SEQ_LVT_FF_nldm_220123.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_SEQ_SLVT_FF_nldm_220123.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_SIMPLE_LVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/asap7sc7p5t_SIMPLE_SLVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/sram_asap7_16x256_1rw.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/sram_asap7_256x128_1rw.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/sram_asap7_32x128_1rw.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/sram_asap7_32x256_1rw.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/sram_asap7_64x256_1rw.lib}]
read_liberty [goalevolve_path {__BENCHMARK_ROOT__/asap7/lib/sram_asap7_64x64_1rw.lib}]
read_def [goalevolve_path {__BENCHMARK_ROOT__/benchmarks/ariane/ariane.def.gz}]
read_verilog [goalevolve_path {__BENCHMARK_ROOT__/benchmarks/ariane/ariane.v}]
read_sdc [goalevolve_path {__BENCHMARK_ROOT__/benchmarks/ariane/ariane.sdc}]
set_ideal_network [all_clocks]
source [goalevolve_path {__BENCHMARK_ROOT__/asap7/setRC.tcl}]
set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um -power mW
set_units -power mW
estimate_parasitics -placement
puts "GOALEVOLVE_CHECKPOINT_BEGIN pre_repair"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC pre_repair tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC pre_repair wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {__OUTPUT_ROOT__/pre_repair.v}]
write_db [goalevolve_path {__OUTPUT_ROOT__/pre_repair.odb}]
puts "GOALEVOLVE_CHECKPOINT_END pre_repair"
set rsz_start [clock seconds]
repair_design
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_design"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_design tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_design wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {__OUTPUT_ROOT__/post_repair_design.v}]
write_db [goalevolve_path {__OUTPUT_ROOT__/post_repair_design.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_repair_design"
set ::env(RSZ_POWER_STAGE_TNS_CEILING_S) 1.5e-05
repair_power -phase early_forced_reclaim -proportion 80 -max_moves 300
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_power"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_power tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_power wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {__OUTPUT_ROOT__/post_repair_power.v}]
write_db [goalevolve_path {__OUTPUT_ROOT__/post_repair_power.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_repair_power"
set ::env(RSZ_GOAL_TNS_ABS_S) 1.85e-06
repair_timing -setup -phases {MT1 TNS LAST_GASP CRIT_VT_SWAP} -sequence {vt_swap sizeup swap sizeup_match buffer} -repair_tns 1 -max_repairs_per_pass 2 -max_passes 2 -max_iterations 2
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_timing"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_timing tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_timing wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {__OUTPUT_ROOT__/post_repair_timing.v}]
write_db [goalevolve_path {__OUTPUT_ROOT__/post_repair_timing.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_repair_timing"
set rsz_end [clock seconds]
puts "\[INFO\] OR RSZ running time:   [expr {$rsz_end - $rsz_start}] seconds"
	set_placement_padding -global -left 0 -right 0
	detailed_placement
	improve_placement -max_displacement {5 1}
	optimize_mirroring
	check_placement -verbose
	estimate_parasitics -placement
	set_power_activity -global -activity 0.1 -duty 0.5
	unset_power_activity -global
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_placement"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_placement tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_placement wns_abs_ns %.12g" [worst_slack -max]]
	report_power -digits 12
write_verilog [goalevolve_path {__OUTPUT_ROOT__/post_placement.v}]
write_db [goalevolve_path {__OUTPUT_ROOT__/post_placement.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_placement"
write_def [goalevolve_path {__OUTPUT_ROOT__/ariane.def}]
write_verilog [goalevolve_path {__OUTPUT_ROOT__/ariane.v}]
	if {[info exists route_signal_layers]} { set signal_layers $route_signal_layers } else { set signal_layers M2-M9 }
	if {[info exists route_clock_layers]} { set clock_layers $route_clock_layers } else { set clock_layers M2-M9 }
	set_routing_layers -signal $signal_layers -clock $clock_layers
	global_route -skip_large_fanout_nets 300 -allow_congestion -congestion_iterations 50
	estimate_parasitics -global_routing
	set_power_activity -global -activity 0.1 -duty 0.5
	unset_power_activity -global
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_route"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_route tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_route wns_abs_ns %.12g" [worst_slack -max]]
	report_power -digits 12
write_verilog [goalevolve_path {__OUTPUT_ROOT__/post_route.v}]
write_db [goalevolve_path {__OUTPUT_ROOT__/post_route.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_route"
puts "===== METRICS ====="
puts "design:                 ariane"
puts [format "total_insts:            %d" [llength [get_cells *]]]
puts "Placement legalized."
report_units
report_tns
report_wns -digits 4
	report_power -digits 12
report_check_types -max_slew -violators
report_check_types -max_capacitance -violators
report_check_types -max_fanout -violators
puts "\[INFO\] Flow running time:   [expr {[clock seconds] - $start}] seconds"
source [goalevolve_path {__PROJECT_VENDOR__/mlcad2026_official/validity_check/OpenROAD_utils.tcl}]
write_node_and_net_files [goalevolve_path {__OUTPUT_ROOT__/node.csv}] [goalevolve_path {__OUTPUT_ROOT__/nets.csv}]
exit
