###############################################################################
# Created by write_sdc
###############################################################################
current_design NV_NVDLA_partition_c
###############################################################################
# Timing Constraints
###############################################################################
create_clock -name nvdla_core_clk -period 650.0000 [get_ports {nvdla_core_clk}]
set_clock_transition 50.0000 [get_clocks {nvdla_core_clk}]
set_false_path\
    -from [list [get_ports {direct_reset_}]\
           [get_ports {dla_reset_rstn}]\
           [get_ports {global_clk_ovr_on}]\
           [get_ports {nvdla_clk_ovr_on}]\
           [get_ports {pwrbus_ram_pd[0]}]\
           [get_ports {pwrbus_ram_pd[10]}]\
           [get_ports {pwrbus_ram_pd[11]}]\
           [get_ports {pwrbus_ram_pd[12]}]\
           [get_ports {pwrbus_ram_pd[13]}]\
           [get_ports {pwrbus_ram_pd[14]}]\
           [get_ports {pwrbus_ram_pd[15]}]\
           [get_ports {pwrbus_ram_pd[16]}]\
           [get_ports {pwrbus_ram_pd[17]}]\
           [get_ports {pwrbus_ram_pd[18]}]\
           [get_ports {pwrbus_ram_pd[19]}]\
           [get_ports {pwrbus_ram_pd[1]}]\
           [get_ports {pwrbus_ram_pd[20]}]\
           [get_ports {pwrbus_ram_pd[21]}]\
           [get_ports {pwrbus_ram_pd[22]}]\
           [get_ports {pwrbus_ram_pd[23]}]\
           [get_ports {pwrbus_ram_pd[24]}]\
           [get_ports {pwrbus_ram_pd[25]}]\
           [get_ports {pwrbus_ram_pd[26]}]\
           [get_ports {pwrbus_ram_pd[27]}]\
           [get_ports {pwrbus_ram_pd[28]}]\
           [get_ports {pwrbus_ram_pd[29]}]\
           [get_ports {pwrbus_ram_pd[2]}]\
           [get_ports {pwrbus_ram_pd[30]}]\
           [get_ports {pwrbus_ram_pd[31]}]\
           [get_ports {pwrbus_ram_pd[3]}]\
           [get_ports {pwrbus_ram_pd[4]}]\
           [get_ports {pwrbus_ram_pd[5]}]\
           [get_ports {pwrbus_ram_pd[6]}]\
           [get_ports {pwrbus_ram_pd[7]}]\
           [get_ports {pwrbus_ram_pd[8]}]\
           [get_ports {pwrbus_ram_pd[9]}]\
           [get_ports {test_mode}]\
           [get_ports {tmc2slcg_disable_clock_gating}]]
###############################################################################
# Environment
###############################################################################
###############################################################################
# Design Rules
###############################################################################
