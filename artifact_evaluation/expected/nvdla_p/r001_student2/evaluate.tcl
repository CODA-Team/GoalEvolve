# Portable Tcl generated from a recorded GoalEvolve_v2 parent flow.
foreach required {GOALEVOLVE_BENCHMARK_ROOT GOALEVOLVE_PROJECT_ROOT GOALEVOLVE_AE_OUTPUT} {
  if {![info exists ::env($required)] || $::env($required) eq ""} {
    error "artifact evaluation requires environment variable $required"
  }
}
proc goalevolve_path {recorded_path} {
  set substitutions [list \
    "/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks" $::env(GOALEVOLVE_BENCHMARK_ROOT) \
    "/home/haixuliu/MLCAD26/GoalEvolve_v2/vendor" [file join $::env(GOALEVOLVE_PROJECT_ROOT) third_party] \
    "/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2" $::env(GOALEVOLVE_AE_OUTPUT)]
  return [string map $substitutions $recorded_path]
}

set start [clock seconds]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7_tech_1x_201209.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_L_1x_220121a.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_SL_1x_220121a.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_SRAM_1x_220121a.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_16x256_1rw.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_256x128_1rw.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_32x128_1rw.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_32x256_1rw.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_64x256_1rw.lef}]
read_lef [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_64x64_1rw.lef}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_AO_LVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_AO_RVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_AO_SLVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_INVBUF_LVT_FF_nldm_220122.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_INVBUF_SLVT_FF_nldm_220122.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_OA_LVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_OA_RVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_OA_SLVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SEQ_LVT_FF_nldm_220123.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SEQ_SLVT_FF_nldm_220123.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SIMPLE_LVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SIMPLE_SLVT_FF_nldm_211120.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_16x256_1rw.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_256x128_1rw.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_32x128_1rw.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_32x256_1rw.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_64x256_1rw.lib}]
read_liberty [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_64x64_1rw.lib}]
read_def [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks/nvdla_p/nvdla_p.def.gz}]
read_verilog [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks/nvdla_p/nvdla_p.v}]
read_sdc [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks/nvdla_p/nvdla_p.sdc}]
set_ideal_network [all_clocks]
source [goalevolve_path {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/setRC.tcl}]
set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um -power mW
set_units -power mW
estimate_parasitics -placement
puts "GOALEVOLVE_CHECKPOINT_BEGIN pre_repair"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC pre_repair tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC pre_repair wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/pre_repair.v}]
write_db [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/pre_repair.odb}]
puts "GOALEVOLVE_CHECKPOINT_END pre_repair"
set rsz_start [clock seconds]
repair_design -max_utilization 90 -slew_margin 10 -cap_margin 10
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_design"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_design tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_design wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_repair_design.v}]
write_db [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_repair_design.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_repair_design"
set ::env(RSZ_POWER_STAGE_TNS_CEILING_S) 2.46e-07
set ::env(RSZ_REPAIR_POWER_MAX_TNS_EXPAND_RATIO) 1
repair_power -phase early_forced_reclaim -proportion 80 -max_tns_expand_ratio 1
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_power"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_power tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_power wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_repair_power.v}]
write_db [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_repair_power.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_repair_power"
set ::env(RSZ_GOAL_TNS_ABS_S) 2.46e-07
repair_timing -setup
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_timing"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_timing tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_timing wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_repair_timing.v}]
write_db [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_repair_timing.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_repair_timing"
repair_design -max_utilization 90 -slew_margin 10 -cap_margin 10
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_optimization_repair_design"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_optimization_repair_design tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_optimization_repair_design wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_optimization_repair_design.v}]
write_db [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_optimization_repair_design.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_optimization_repair_design"
set rsz_end [clock seconds]
puts "\[INFO\] OR RSZ running time:   [expr {$rsz_end - $rsz_start}] seconds"
detailed_placement
check_placement -verbose
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_placement"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_placement tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_placement wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_placement.v}]
write_db [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_placement.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_placement"
write_def [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/nvdla_p.def}]
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/nvdla_p.v}]
if {[info exists route_signal_layers]} { set signal_layers $route_signal_layers } else { set signal_layers M2-M9 }
if {[info exists route_clock_layers]} { set clock_layers $route_clock_layers } else { set clock_layers M2-M9 }
set_routing_layers -signal $signal_layers -clock $clock_layers
global_route -skip_large_fanout_nets 300 -allow_congestion -congestion_iterations 50
estimate_parasitics -global_routing
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_route"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_route tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_route wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_route.v}]
write_db [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/post_route.odb}]
puts "GOALEVOLVE_CHECKPOINT_END post_route"
puts "===== METRICS ====="
puts "design:                 nvdla_p"
puts [format "total_insts:            %d" [llength [get_cells *]]]
puts "Placement legalized."
report_units
report_tns
report_wns -digits 4
report_power
report_check_types -max_slew -violators
report_check_types -max_capacitance -violators
report_check_types -max_fanout -violators
puts "\[INFO\] Flow running time:   [expr {[clock seconds] - $start}] seconds"
source [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/vendor/official_checker/validity_check/OpenROAD_utils.tcl}]
write_node_and_net_files [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/node.csv}] [goalevolve_path {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_p_campaign/evolution_top1_restart/stage_baselines/b2e98c139211cad5d066eab4e2e9e86a9ec528163702041d9c4303798bcec3f3_power_then_timing_405a72667d13eae2/nets.csv}]
exit
