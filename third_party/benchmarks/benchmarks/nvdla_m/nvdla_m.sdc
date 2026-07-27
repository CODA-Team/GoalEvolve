###############################################################################
# Created by write_sdc
###############################################################################
current_design NV_NVDLA_partition_m
###############################################################################
# Timing Constraints
###############################################################################
create_clock -name nvdla_core_clk -period 400.0000 [get_ports {nvdla_core_clk}]
set_clock_transition 0.1000 [get_clocks {nvdla_core_clk}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[0]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[10]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[11]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[12]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[13]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[14]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[15]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[16]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[17]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[18]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[19]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[1]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[20]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[21]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[22]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[23]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[24]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[25]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[26]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[27]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[28]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[29]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[2]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[30]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[31]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[32]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[33]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[34]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[35]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[36]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[37]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[38]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[39]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[3]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[40]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[41]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[42]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[43]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[44]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[45]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[46]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[47]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[48]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[49]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[4]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[50]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[51]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[52]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[53]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[54]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[55]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[56]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[57]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[58]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[59]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[5]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[60]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[61]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[62]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[6]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[7]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[8]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pd[9]}]
set_input_delay 0.0000 -add_delay [get_ports {csb2cmac_a_req_pvld}]
set_input_delay 0.0000 -add_delay [get_ports {direct_reset_}]
set_input_delay 0.0000 -add_delay [get_ports {dla_reset_rstn}]
set_input_delay 0.0000 -add_delay [get_ports {global_clk_ovr_on}]
set_input_delay 0.0000 -add_delay [get_ports {nvdla_clk_ovr_on}]
set_input_delay 0.0000 -add_delay [get_ports {nvdla_core_clk}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data0[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data1[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data2[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data3[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data4[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data5[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data6[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_data7[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_mask[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pd[8]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_dat_pvld}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data0[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data1[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data2[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data3[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data4[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data5[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data6[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_data7[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[3]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[4]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[5]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[6]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_mask[7]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_pvld}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_sel[0]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_sel[1]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_sel[2]}]
set_input_delay 0.0000 -add_delay [get_ports {sc2mac_wt_sel[3]}]
set_input_delay 0.0000 -add_delay [get_ports {test_mode}]
set_input_delay 0.0000 -add_delay [get_ports {tmc2slcg_disable_clock_gating}]
set_false_path\
    -from [list [get_ports {direct_reset_}]\
           [get_ports {dla_reset_rstn}]\
           [get_ports {global_clk_ovr_on}]\
           [get_ports {nvdla_clk_ovr_on}]\
           [get_ports {test_mode}]\
           [get_ports {tmc2slcg_disable_clock_gating}]]
set_false_path\
    -to [list [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_nz_reg3[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_pvld_reg3[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_pvld_reg3[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_stripe_end$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_actv_stripe_end$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_pvld$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_pvld$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_stripe_end$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_stripe_end$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_stripe_st$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.dat_pre_stripe_st$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_vld$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_actv_vld$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_pvld$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt0_sd_pvld$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_vld$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_actv_vld$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_pvld$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt1_sd_pvld$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_vld$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_actv_vld$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_pvld$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt2_sd_pvld$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_pvld[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_actv_pvld[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_pvld$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt3_sd_pvld$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_nz[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[1]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[1]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[2]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[2]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[3]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_active.wt_pre_sel[3]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d1[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_mask_d2[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[8]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d1[8]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[8]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pd_d2[8]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pvld_d1$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pvld_d1$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pvld_d2$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_dat_pvld_d2$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d1[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_mask_d2[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_pvld_d1$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_pvld_d1$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_pvld_d2$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_pvld_d2$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d1[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_in.in_rt_wt_sel_d2[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_done_d1$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_done_d1$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_done_d2$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_done_d2$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_done_d3$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_done_d3$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[1]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[1]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[2]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[2]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[3]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d1[3]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[1]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[1]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[2]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[2]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[3]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_mask_d2[3]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[8]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d1[8]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[8]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pd_d2[8]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pvld_d1$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pvld_d1$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pvld_d2$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_core.u_rt_out.out_rt_pvld_d2$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[12]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[12]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[13]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[13]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[16]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[16]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[17]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[17]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[33]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_pd[33]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_valid$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.cmac_a2csb_resp_valid$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.dp2reg_consumer$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.dp2reg_consumer$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.reg2dp_d0_op_en$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.reg2dp_d0_op_en$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.reg2dp_d1_op_en$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.reg2dp_d1_op_en$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[0]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[0]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[22]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[22]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[2]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[2]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[34]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[34]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[35]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[35]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[3]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[3]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[4]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[4]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[54]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[54]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[55]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[55]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[5]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[5]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[6]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[6]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[7]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[7]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[8]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[8]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[9]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pd[9]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pvld$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.req_pvld$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.slcg_op_en_d1[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.slcg_op_en_d1[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.slcg_op_en_d2[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.slcg_op_en_d2[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.slcg_op_en_d3[0]$_DFF_PN0_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.slcg_op_en_d3[0]$_DFF_PN0_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d0.conv_mode$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d0.conv_mode$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d0.proc_precision[0]$_DFFE_PN1P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d0.proc_precision[0]$_DFFE_PN1P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d0.proc_precision[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d0.proc_precision[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d1.conv_mode$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d1.conv_mode$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d1.proc_precision[0]$_DFFE_PN1P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d1.proc_precision[0]$_DFFE_PN1P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d1.proc_precision[1]$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_dual_reg_d1.proc_precision[1]$_DFFE_PN0P_/SETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_single_reg.producer$_DFFE_PN0P_/RESETN}]\
           [get_pins {u_NV_NVDLA_cmac.u_reg.u_single_reg.producer$_DFFE_PN0P_/SETN}]\
           [get_pins {u_global_clk_ovr_on_sync.sync_0.NV_GENERIC_CELL.d0$_DFF_PN1_/RESETN}]\
           [get_pins {u_global_clk_ovr_on_sync.sync_0.NV_GENERIC_CELL.d0$_DFF_PN1_/SETN}]\
           [get_pins {u_global_clk_ovr_on_sync.sync_0.NV_GENERIC_CELL.d1$_DFF_PN1_/RESETN}]\
           [get_pins {u_global_clk_ovr_on_sync.sync_0.NV_GENERIC_CELL.d1$_DFF_PN1_/SETN}]\
           [get_pins {u_global_clk_ovr_on_sync.sync_0.NV_GENERIC_CELL.q$_DFF_PN1_/RESETN}]\
           [get_pins {u_global_clk_ovr_on_sync.sync_0.NV_GENERIC_CELL.q$_DFF_PN1_/SETN}]\
           [get_pins {u_partition_m_reset.sync_reset_synced_rstn.NV_GENERIC_CELL.d0$_DFF_PN0_/RESETN}]\
           [get_pins {u_partition_m_reset.sync_reset_synced_rstn.NV_GENERIC_CELL.d0$_DFF_PN0_/SETN}]\
           [get_pins {u_partition_m_reset.sync_reset_synced_rstn.NV_GENERIC_CELL.q$_DFF_PN0_/RESETN}]\
           [get_pins {u_partition_m_reset.sync_reset_synced_rstn.NV_GENERIC_CELL.q$_DFF_PN0_/SETN}]]
###############################################################################
# Environment
###############################################################################
set_wire_load_mode "enclosed"
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_valid}]
set_load -pin_load 0.0000 [get_ports {csb2cmac_a_req_prdy}]
set_load -pin_load 0.0000 [get_ports {mac2accu_mode}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pvld}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[33]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[32]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[31]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[30]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[29]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[28]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[27]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[26]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[25]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[24]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[23]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[22]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[21]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[20]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[19]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[18]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[17]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[16]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[15]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[14]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[13]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[12]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[11]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[10]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[9]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[8]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[7]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[6]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[5]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[4]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[3]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[2]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[1]}]
set_load -pin_load 0.0000 [get_ports {cmac_a2csb_resp_pd[0]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[18]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[17]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[16]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[15]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[14]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[13]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[12]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[11]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[10]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[9]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[8]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[7]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[6]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[5]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[4]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[3]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[2]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[1]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data0[0]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[18]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[17]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[16]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[15]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[14]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[13]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[12]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[11]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[10]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[9]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[8]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[7]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[6]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[5]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[4]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[3]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[2]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[1]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data1[0]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[18]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[17]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[16]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[15]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[14]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[13]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[12]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[11]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[10]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[9]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[8]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[7]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[6]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[5]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[4]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[3]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[2]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[1]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data2[0]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[18]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[17]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[16]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[15]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[14]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[13]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[12]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[11]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[10]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[9]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[8]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[7]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[6]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[5]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[4]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[3]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[2]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[1]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_data3[0]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_mask[3]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_mask[2]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_mask[1]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_mask[0]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[8]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[7]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[6]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[5]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[4]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[3]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[2]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[1]}]
set_load -pin_load 0.0000 [get_ports {mac2accu_pd[0]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pvld}]
set_input_transition 0.1000 [get_ports {direct_reset_}]
set_input_transition 0.1000 [get_ports {dla_reset_rstn}]
set_input_transition 0.1000 [get_ports {global_clk_ovr_on}]
set_input_transition 0.1000 [get_ports {nvdla_clk_ovr_on}]
set_input_transition 0.1000 [get_ports {nvdla_core_clk}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pvld}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_pvld}]
set_input_transition 0.1000 [get_ports {test_mode}]
set_input_transition 0.1000 [get_ports {tmc2slcg_disable_clock_gating}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[62]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[61]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[60]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[59]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[58]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[57]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[56]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[55]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[54]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[53]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[52]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[51]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[50]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[49]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[48]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[47]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[46]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[45]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[44]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[43]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[42]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[41]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[40]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[39]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[38]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[37]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[36]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[35]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[34]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[33]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[32]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[31]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[30]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[29]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[28]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[27]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[26]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[25]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[24]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[23]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[22]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[21]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[20]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[19]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[18]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[17]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[16]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[15]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[14]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[13]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[12]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[11]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[10]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[9]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[8]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[7]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[6]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[5]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[4]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[3]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[2]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[1]}]
set_input_transition 0.1000 [get_ports {csb2cmac_a_req_pd[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data0[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data1[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data2[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data3[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data4[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data5[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data6[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_data7[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_mask[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[8]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_dat_pd[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data0[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data1[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data2[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data3[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data4[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data5[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data6[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_data7[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[7]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[6]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[5]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[4]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_mask[0]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_sel[3]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_sel[2]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_sel[1]}]
set_input_transition 0.1000 [get_ports {sc2mac_wt_sel[0]}]
###############################################################################
# Design Rules
###############################################################################
set_max_fanout 128.0000 [current_design]
