set start [clock seconds]
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7_tech_1x_201209.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_L_1x_220121a.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_SL_1x_220121a.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/asap7sc7p5t_28_SRAM_1x_220121a.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_16x256_1rw.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_256x128_1rw.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_32x128_1rw.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_32x256_1rw.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_64x256_1rw.lef}
read_lef {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lef/sram_asap7_64x64_1rw.lef}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_AO_LVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_AO_RVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_AO_SLVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_INVBUF_LVT_FF_nldm_220122.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_INVBUF_SLVT_FF_nldm_220122.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_OA_LVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_OA_RVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_OA_SLVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SEQ_LVT_FF_nldm_220123.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SEQ_SLVT_FF_nldm_220123.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SIMPLE_LVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/asap7sc7p5t_SIMPLE_SLVT_FF_nldm_211120.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_16x256_1rw.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_256x128_1rw.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_32x128_1rw.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_32x256_1rw.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_64x256_1rw.lib}
read_liberty {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/lib/sram_asap7_64x64_1rw.lib}
read_def {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks/nvdla_a/nvdla_a.def.gz}
read_verilog {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks/nvdla_a/nvdla_a.v}
read_sdc {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks/nvdla_a/nvdla_a.sdc}
set_ideal_network [all_clocks]
source {/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/asap7/setRC.tcl}
set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um -power mW
set_units -power mW
estimate_parasitics -placement
puts "GOALEVOLVE_CHECKPOINT_BEGIN pre_repair"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC pre_repair tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC pre_repair wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/pre_repair.v}
write_db {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/pre_repair.odb}
puts "GOALEVOLVE_CHECKPOINT_END pre_repair"
set rsz_start [clock seconds]
puts "GOALEVOLVE_INITIAL_REPAIR_DESIGN_SKIPPED explicit_campaign_config=true"
set ::env(RSZ_POWER_STAGE_TNS_CEILING_S) 2.94e-07
set ::env(RSZ_TIMING_RECIPE_ID) {legacy_setup}
set ::env(RSZ_REPAIR_POWER_MAX_TNS_EXPAND_RATIO) 1.2
set ::env(RSZ_REPAIR_POWER_MAX_WNS_DROP) 2e-09
set ::env(RSZ_REPAIR_POWER_MIN_TARGET_SLACK) -1e-06
set ::env(RSZ_REPAIR_POWER_MAX_TARGETS) 50000
set ::env(RSZ_REPAIR_POWER_TRIAL_LIMIT) 120000
set ::env(RSZ_REPAIR_POWER_BATCH_SIZE) 512
repair_power -phase early_forced_reclaim -proportion 100 -max_moves 12000 -max_tns_expand_ratio 1.2 -max_wns_drop 2
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_power"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_power tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_repair_power wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/post_repair_power.v}
write_db {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/post_repair_power.odb}
puts "GOALEVOLVE_CHECKPOINT_END post_repair_power"
set rsz_end [clock seconds]
puts "\[INFO\] OR RSZ running time:   [expr {$rsz_end - $rsz_start}] seconds"
detailed_placement
check_placement -verbose
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_placement"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_placement tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_placement wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/post_placement.v}
write_db {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/post_placement.odb}
puts "GOALEVOLVE_CHECKPOINT_END post_placement"
write_def {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/nvdla_a.def}
write_verilog {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/nvdla_a.v}
if {[info exists route_signal_layers]} { set signal_layers $route_signal_layers } else { set signal_layers M2-M9 }
if {[info exists route_clock_layers]} { set clock_layers $route_clock_layers } else { set clock_layers M2-M9 }
set_routing_layers -signal $signal_layers -clock $clock_layers
global_route -skip_large_fanout_nets 300 -allow_congestion -congestion_iterations 50
estimate_parasitics -global_routing
puts "GOALEVOLVE_CHECKPOINT_BEGIN post_route"
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_route tns_abs_ns %.12g" [total_negative_slack -max]]
puts [format "GOALEVOLVE_CHECKPOINT_METRIC post_route wns_abs_ns %.12g" [worst_slack -max]]
report_power
write_verilog {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/post_route.v}
write_db {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/post_route.odb}
puts "GOALEVOLVE_CHECKPOINT_END post_route"
puts "===== METRICS ====="
puts "design:                 nvdla_a"
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
source {/home/haixuliu/MLCAD26/GoalEvolve_v2/vendor/mlcad2026_official/validity_check/OpenROAD_utils.tcl}
write_node_and_net_files {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/node.csv} {/home/haixuliu/MLCAD26/GoalEvolve_v2/runtime/nvdla_a_evolution/campaign_aes_r58_power_target/rounds/round_019/students/student_1/artifacts/contest_output/nets.csv}
exit
