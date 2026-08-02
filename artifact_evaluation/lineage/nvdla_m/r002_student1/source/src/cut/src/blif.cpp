// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2019-2025, The OpenROAD Authors

#include "cut/blif.h"

#include <algorithm>
#include <cstdlib>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "cut/blifParser.h"
#include "db_sta/dbNetwork.hh"
#include "db_sta/dbSta.hh"
#include "odb/db.h"
#include "sta/Delay.hh"
#include "sta/FuncExpr.hh"
#include "sta/Graph.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/Path.hh"
#include "sta/PortDirection.hh"
#include "sta/Sta.hh"
#include "utl/Logger.h"

using utl::CUT;

namespace cut {

namespace {

bool startsWith(const std::string& value, const char* prefix)
{
  return value.rfind(prefix, 0) == 0;
}

bool envIsSet(const char* name)
{
  return std::getenv(name) != nullptr;
}

int envInt(const char* name, const int default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  char* end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  return end == value ? default_value : static_cast<int>(parsed);
}

int snapDownToGrid(const int value, const int origin, const int step)
{
  if (step <= 0) {
    return value;
  }
  const int delta = value - origin;
  if (delta >= 0) {
    return origin + (delta / step) * step;
  }
  return origin - ((-delta + step - 1) / step) * step;
}

bool snapToPlacementRow(odb::dbBlock* block,
                        odb::dbMaster* master,
                        const int raw_x,
                        const int raw_y,
                        int& snapped_x,
                        int& snapped_y,
                        odb::dbOrientType& snapped_orient)
{
  if (block == nullptr || master == nullptr) {
    return false;
  }

  odb::dbRow* best_row = nullptr;
  int64_t best_dist = std::numeric_limits<int64_t>::max();
  const int master_width = static_cast<int>(master->getWidth());
  for (odb::dbRow* row : block->getRows()) {
    odb::dbSite* site = row->getSite();
    if (site == nullptr || site->getClass() == odb::dbSiteClass::PAD) {
      continue;
    }
    const odb::Point row_origin = row->getOrigin();
    const int64_t dy = static_cast<int64_t>(raw_y) - row_origin.y();
    const int64_t dist = dy * dy;
    if (dist < best_dist) {
      best_dist = dist;
      best_row = row;
    }
  }
  if (best_row == nullptr) {
    return false;
  }

  const odb::Point origin = best_row->getOrigin();
  const int spacing = best_row->getSpacing();
  const int site_count = best_row->getSiteCount();
  const int row_min_x = origin.x();
  int row_max_x = row_min_x;
  if (site_count > 0 && spacing > 0) {
    row_max_x = row_min_x + (site_count - 1) * spacing;
  }
  row_max_x = std::max(row_min_x, row_max_x - std::max(0, master_width - spacing));

  int x = snapDownToGrid(raw_x, row_min_x, spacing);
  x = std::clamp(x, row_min_x, row_max_x);
  x = snapDownToGrid(x, row_min_x, spacing);

  snapped_x = x;
  snapped_y = origin.y();
  snapped_orient = best_row->getOrient();
  return true;
}

bool isSupportedAdderCell(const std::string& master_name)
{
  return startsWith(master_name, "FAx") || startsWith(master_name, "HAx");
}

std::string normalizeAsap7VtForAbc(std::string master_name)
{
  if (std::getenv("RMP_NORMALIZE_BLIF_TO_SLVT") == nullptr) {
    return master_name;
  }

  const std::string rvt_suffix = "_ASAP7_75t_R";
  const std::string lvt_suffix = "_ASAP7_75t_L";
  const std::string slvt_suffix = "_ASAP7_75t_SL";
  if (master_name.size() >= rvt_suffix.size()
      && master_name.compare(master_name.size() - rvt_suffix.size(),
                             rvt_suffix.size(),
                             rvt_suffix)
             == 0) {
    master_name.replace(master_name.size() - rvt_suffix.size(),
                        rvt_suffix.size(),
                        slvt_suffix);
  } else if (master_name.size() >= lvt_suffix.size()
             && master_name.compare(master_name.size() - lvt_suffix.size(),
                                    lvt_suffix.size(),
                                    lvt_suffix)
                    == 0) {
    master_name.replace(master_name.size() - lvt_suffix.size(),
                        lvt_suffix.size(),
                        slvt_suffix);
  }
  if (std::getenv("RMP_NORMALIZE_BLIF_TO_STRONG") != nullptr) {
    const std::string hb_prefix = "HB";
    if (startsWith(master_name, hb_prefix.c_str())) {
      master_name.replace(0, master_name.find("_ASAP7_75t_SL"), "BUFx3");
    }
  }
  return master_name;
}

std::string emitNames(const std::vector<std::string>& inputs,
                      const std::string& output,
                      const std::vector<std::string>& on_set)
{
  if (output.empty()) {
    return "";
  }
  std::ostringstream out;
  out << ".names";
  for (const std::string& input : inputs) {
    if (input.empty()) {
      return "";
    }
    out << " " << input;
  }
  out << " " << output << "\n";
  for (const std::string& row : on_set) {
    out << row << " 1\n";
  }
  return out.str();
}

std::string emitAdderNames(const std::string& master_name,
                           const std::map<std::string, std::string>& nets)
{
  auto net = [&](const char* pin) -> std::string {
    const auto found = nets.find(pin);
    return found == nets.end() ? "" : found->second;
  };

  if (startsWith(master_name, "FAx")) {
    const std::vector<std::string> inputs = {net("A"), net("B"), net("CI")};
    std::string out;
    // ASAP7 FA SN is even parity; CON is inverted carry.
    out += emitNames(inputs, net("SN"), {"000", "011", "101", "110"});
    out += emitNames(inputs, net("CON"), {"000", "001", "010", "100"});
    return out;
  }

  if (startsWith(master_name, "HAx")) {
    const std::vector<std::string> inputs = {net("A"), net("B")};
    std::string out;
    // ASAP7 HA SN is XNOR; CON is NAND.
    out += emitNames(inputs, net("SN"), {"00", "11"});
    out += emitNames(inputs, net("CON"), {"00", "01", "10"});
    return out;
  }

  return "";
}

bool evalFuncExpr(const sta::FuncExpr* expr,
                  const std::map<const sta::LibertyPort*, bool>& values)
{
  switch (expr->op()) {
    case sta::FuncExpr::Op::port: {
      const auto found = values.find(expr->port());
      return found != values.end() && found->second;
    }
    case sta::FuncExpr::Op::not_:
      return !evalFuncExpr(expr->left(), values);
    case sta::FuncExpr::Op::or_:
      return evalFuncExpr(expr->left(), values)
             || evalFuncExpr(expr->right(), values);
    case sta::FuncExpr::Op::and_:
      return evalFuncExpr(expr->left(), values)
             && evalFuncExpr(expr->right(), values);
    case sta::FuncExpr::Op::xor_:
      return evalFuncExpr(expr->left(), values)
             != evalFuncExpr(expr->right(), values);
    case sta::FuncExpr::Op::one:
      return true;
    case sta::FuncExpr::Op::zero:
      return false;
  }
  return false;
}

std::string emitFuncNames(sta::LibertyCell* cell,
                          const std::map<std::string, std::string>& nets)
{
  sta::LibertyPort* output_port = nullptr;
  std::vector<sta::LibertyPort*> input_ports;

  sta::LibertyCellPortIterator port_iter(cell);
  while (port_iter.hasNext()) {
    sta::LibertyPort* port = port_iter.next();
    const auto* dir = port->direction();
    if (dir->isAnyInput() && !port->isClock() && !port->isPwrGnd()) {
      input_ports.push_back(port);
    } else if (dir->isAnyOutput() && port->function() != nullptr
               && port->tristateEnable() == nullptr && !port->isPwrGnd()) {
      if (output_port != nullptr) {
        return "";
      }
      output_port = port;
    }
  }

  if (output_port == nullptr) {
    return "";
  }
  const sta::FuncExpr* expr = output_port->function();
  const sta::LibertyPortSet expr_ports = expr->ports();
  if (static_cast<int>(expr_ports.size())
      > envInt("RMP_WRITE_BLIF_NAMES_MAX_INPUTS", 8)) {
    return "";
  }

  std::vector<std::string> input_nets;
  std::vector<sta::LibertyPort*> expr_input_ports;
  for (sta::LibertyPort* port : input_ports) {
    if (!expr_ports.contains(port)) {
      continue;
    }
    const auto found = nets.find(port->name());
    if (found == nets.end() || found->second.empty()) {
      return "";
    }
    expr_input_ports.push_back(port);
    input_nets.push_back(found->second);
  }
  if (expr_input_ports.size() != expr_ports.size()) {
    return "";
  }

  const auto output_found = nets.find(output_port->name());
  if (output_found == nets.end() || output_found->second.empty()) {
    return "";
  }

  std::vector<std::string> on_set;
  const size_t row_count = size_t{1} << expr_input_ports.size();
  for (size_t row = 0; row < row_count; ++row) {
    std::map<const sta::LibertyPort*, bool> values;
    std::string bits;
    for (size_t i = 0; i < expr_input_ports.size(); ++i) {
      const bool value = (row & (size_t{1} << (expr_input_ports.size() - i - 1)))
                         != 0;
      values[expr_input_ports[i]] = value;
      bits += value ? '1' : '0';
    }
    if (evalFuncExpr(expr, values)) {
      on_set.push_back(bits);
    }
  }

  return emitNames(input_nets, output_found->second, on_set);
}

}  // namespace

Blif::Blif(utl::Logger* logger,
           sta::dbSta* sta,
           const std::string& const0_cell,
           const std::string& const0_cell_port,
           const std::string& const1_cell,
           const std::string& const1_cell_port,
           const int call_id)
    : const0_cell_(const0_cell),
      const0_cell_port_(const0_cell_port),
      const1_cell_(const1_cell),
      const1_cell_port_(const1_cell_port),
      call_id_(call_id)
{
  logger_ = logger;
  open_sta_ = sta;
}

void Blif::setReplaceableInstances(std::set<odb::dbInst*>& insts)
{
  instances_to_optimize_ = insts;
}

void Blif::addReplaceableInstance(odb::dbInst* inst)
{
  instances_to_optimize_.insert(inst);
}

void Blif::setAllowedOutputNets(const std::set<std::string>& net_names)
{
  allowed_output_nets_ = net_names;
}

bool Blif::writeBlif(const char* file_name, bool write_arrival_requireds)
{
  int dummy_nets = 0;

  std::ofstream f(file_name);

  // These always need to be done before writing blif
  open_sta_->ensureGraph();
  open_sta_->ensureLevelized();
  open_sta_->searchPreamble();

  if (f.bad()) {
    logger_->error(CUT, 1, "Cannot open file {}.", file_name);
    return false;
  }

  std::set<odb::dbInst*>& insts = this->instances_to_optimize_;
  std::map<uint32_t, odb::dbInst*> inst_map;
  std::vector<std::string> subckts;
  std::set<std::string> inputs, outputs, const0, const1, clocks;

  subckts.resize(insts.size());
  int inst_index = 0;

  for (auto&& inst : insts) {
    inst_map.insert(std::pair<uint32_t, odb::dbInst*>(inst->getId(), inst));
  }

  for (auto&& inst : insts) {
    auto master = inst->getMaster();
    sta::LibertyCell* cell = open_sta_->getDbNetwork()->libertyCell(
        open_sta_->getDbNetwork()->dbToSta(master));
    const std::string master_name = master->getName();
    const std::string abc_master_name = normalizeAsap7VtForAbc(master_name);

    std::string current_gate
        = ((cell->hasSequentials()) ? ".mlatch " : ".gate ") + abc_master_name;
    std::string current_connections, current_clock;
    std::set<std::string> current_clocks;
    std::map<std::string, std::string> pin_nets;

    auto iterms = inst->getITerms();

    for (auto&& iterm : iterms) {
      auto mterm = iterm->getMTerm();
      auto net = iterm->getNet();

      if (iterm->getSigType() == odb::dbSigType::POWER
          || iterm->getSigType() == odb::dbSigType::GROUND) {
        continue;
      }

      sta::Vertex *vertex, *bidirect_drvr_vertex;
      auto pin = open_sta_->getDbNetwork()->dbToSta(iterm);
      open_sta_->getDbNetwork()->graph()->pinVertices(
          pin, vertex, bidirect_drvr_vertex);
      auto network = open_sta_->network();
      auto port = network->libertyPort(pin);
      if (port->isClock()) {
        if (net == nullptr) {
          continue;
        }
        clocks.insert(net->getName());
        current_clocks.insert(net->getName());
        current_clock = net->getName();
        continue;
      }

      const auto& mterm_name = mterm->getName();
      const auto& net_name = (net == nullptr)
                                 ? ("dummy_" + std::to_string(dummy_nets++))
                                 : net->getName();

      current_connections += fmt::format(" {}={}", mterm_name, net_name);
      pin_nets[mterm_name] = net_name;

      if (net == nullptr) {
        continue;
      }
      // check whether connected net is input/output
      // If it's only connected to one Iterm OR
      // It's connected to another instance that's outside the bubble
      auto connected_iterms = net->getITerms();

      if (connected_iterms.size() == 1) {
        if (iterm->getIoType() == odb::dbIoType::INPUT) {
          inputs.insert(net_name);
          addArrival(pin, net_name);
        } else if (iterm->getIoType() == odb::dbIoType::OUTPUT) {
          outputs.insert(net_name);
          addRequired(pin, net_name);
        }

      } else {
        bool add_as_input = false;
        for (auto&& connected_iterm : connected_iterms) {
          auto connected_inst_id = connected_iterm->getInst()->getId();

          if (inst_map.find(connected_inst_id) == inst_map.end()) {
            // Net is connected to an instance outside the cut out region
            // Check whether it's input or output
            if (iterm->getIoType() == odb::dbIoType::INPUT) {
              // Net is connected to a pin outside the bubble and should be
              // treated as an input If the driving pin is contant then we'll
              // add a constant gate in blif otherwise just add the net as input
              auto pin = open_sta_->getDbNetwork()->dbToSta(connected_iterm);
              auto network = open_sta_->network();
              auto port = network->libertyPort(pin);

              if (port) {
                auto expr = port->function();
                if (expr
                    // Tristate outputs do not force the output to be constant.
                    && port->tristateEnable() == nullptr
                    && (expr->op() == sta::FuncExpr::Op::zero
                        || expr->op() == sta::FuncExpr::Op::one)) {
                  if (expr->op() == sta::FuncExpr::Op::zero) {
                    if (const0.empty()) {
                      const0_cell_ = port->libertyCell()->name();
                      const0_cell_port_ = port->name();
                    }
                    const0.insert(net_name);
                  } else {
                    if (const1.empty()) {
                      const1_cell_ = port->libertyCell()->name();
                      const1_cell_port_ = port->name();
                    }
                    const1.insert(net_name);
                  }

                } else {
                  add_as_input = true;
                }
              } else {
                add_as_input = true;
              }

            } else if (iterm->getIoType() == odb::dbIoType::OUTPUT) {
              outputs.insert(net_name);
              addRequired(pin, net_name);
            }
          }
        }
        if (add_as_input && const0.find(net_name) == const0.end()
            && const1.find(net_name) == const1.end()) {
          inputs.insert(net_name);
          addArrival(pin, net_name);
        }
      }

      // connect to original ports if not inferred already
      if (inputs.find(net_name) == inputs.end()
          && outputs.find(net_name) == outputs.end()
          && const0.find(net_name) == const0.end()
          && const1.find(net_name) == const1.end()) {
        auto connected_ports = net->getBTerms();
        for (auto connected_port : connected_ports) {
          if (connected_port->getIoType() == odb::dbIoType::INPUT) {
            auto pin = open_sta_->getDbNetwork()->dbToSta(connected_port);
            auto network = open_sta_->network();
            auto port = network->libertyPort(pin);

            if (port) {
              auto expr = port->function();
              if (expr
                  // Tristate outputs do not force the output to be constant.
                  && port->tristateEnable() == nullptr
                  && (expr->op() == sta::FuncExpr::Op::zero
                      || expr->op() == sta::FuncExpr::Op::one)) {
                if (expr->op() == sta::FuncExpr::Op::zero) {
                  const0.insert(net_name);
                } else {
                  const1.insert(net_name);
                }

              } else {
                inputs.insert(net_name);
                addArrival(pin, net_name);
              }
            } else {
              inputs.insert(net_name);
              addArrival(pin, net_name);
            }
          } else if (connected_port->getIoType() == odb::dbIoType::OUTPUT) {
            outputs.insert(net_name);
            addRequired(pin, net_name);
          }
        }
      }
    }

    if (current_connections.empty()) {
      const std::string inst_name = inst->getName();
      logger_->report("RMP_GUARD|blif_empty_gate|inst={}|master={}",
                      inst_name,
                      master_name);
      return false;
    }

    if (isSupportedAdderCell(master_name) && !envIsSet("RMP_DISABLE_ADDER_NAMES")) {
      const std::string adder_names = emitAdderNames(master_name, pin_nets);
      if (!adder_names.empty()) {
        subckts[inst_index++] = adder_names;
        continue;
      }
    }

    if (envIsSet("RMP_WRITE_BLIF_NAMES") && !cell->hasSequentials()) {
      const std::string func_names = emitFuncNames(cell, pin_nets);
      if (!func_names.empty()) {
        subckts[inst_index++] = func_names;
        continue;
      }
      if (envIsSet("RMP_WRITE_BLIF_NAMES_REQUIRE_ALL")) {
        logger_->report("RMP_GUARD|blif_names_failed|inst={}|master={}",
                        inst->getName(),
                        master_name);
        return false;
      }
    }

    current_gate += current_connections;

    if (cell->hasSequentials() && current_clocks.size() != 1) {
      continue;
    }
    if (cell->hasSequentials()) {
      current_gate += " " + current_clock;
    }

    subckts[inst_index++] = std::move(current_gate);
  }

  // remove drivers from input list
  std::vector<std::string> common_ports;
  std::ranges::set_intersection(
      inputs, outputs, std::back_inserter(common_ports));

  for (auto&& port : common_ports) {
    inputs.erase(port);
    arrivals_.erase(port);
  }

  if (!allowed_output_nets_.empty()) {
    std::set<std::string> filtered_outputs;
    std::ranges::set_intersection(outputs,
                                  allowed_output_nets_,
                                  std::inserter(filtered_outputs,
                                                filtered_outputs.end()));
    if (filtered_outputs.empty()) {
      std::ostringstream allowed;
      for (const std::string& net_name : allowed_output_nets_) {
        allowed << (allowed.tellp() == std::streampos(0) ? "" : ",")
                << net_name;
      }
      logger_->report("RMP_GUARD|blif_target_output_missing|allowed={}",
                      allowed.str());
      return false;
    }
    for (auto it = requireds_.begin(); it != requireds_.end();) {
      if (filtered_outputs.count(it->first) == 0) {
        it = requireds_.erase(it);
      } else {
        ++it;
      }
    }
    logger_->report("RMP_GUARD|blif_filter_outputs|before={}|after={}",
                    outputs.size(),
                    filtered_outputs.size());
    outputs = std::move(filtered_outputs);
  }

  f << ".model tmp_circuit\n";
  f << ".inputs";

  for (auto& input : inputs) {
    if (const0.find(input) != const0.end()
        || const1.find(input) != const1.end()) {
      continue;
    }

    f << " " << input;
  }
  f << "\n";

  f << ".outputs";

  for (auto& output : outputs) {
    f << " " << output;
  }
  f << "\n";

  if (!clocks.empty()) {
    f << ".clock";
    for (auto& clock : clocks) {
      f << " " << clock;
    }
  }

  if (write_arrival_requireds) {
    for (auto& arrival : arrivals_) {
      f << ".input_arrival " << arrival.first << " " << arrival.second.first
        << " " << arrival.second.second << '\n';
    }

    for (auto& required : requireds_) {
      f << ".output_required " << required.first << " " << required.second.first
        << " " << required.second.second << '\n';
    }
  }

  f << "\n\n";

  for (auto& zero : const0) {
    std::string const_subctk = ".gate _const0_ z=" + zero;
    f << const_subctk << "\n";
  }

  for (auto& one : const1) {
    std::string const_subctk = ".gate _const1_ z=" + one;
    f << const_subctk << "\n";
  }

  for (auto& subckt : subckts) {
    f << subckt << "\n";
  }

  f << ".end\n";

  f.close();

  logger_->info(CUT,
                2,
                "Blif writer successfully dumped file with {} instances.",
                inst_index);

  return true;
}

void preprocessString(std::string& s)
{
  int ind, old_ind = -1;

  while ((ind = s.find('\n', old_ind + 1)) != std::string::npos) {
    if (s[old_ind + 1] == '#') {
      s.erase(old_ind + 1, ind - old_ind);
    }
    old_ind = ind;
  }
}

bool Blif::inspectBlif(const char* file_name, int& num_instances)
{
  std::ifstream f(file_name);
  if (f.bad()) {
    logger_->error(CUT, 3, "Cannot open file {}.", file_name);
    return false;
  }

  std::string file_string((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());

  // Remove Comment Lines from Blif
  preprocessString(file_string);

  BlifParser blif;

  bool is_valid = blif.parse(file_string);

  if (is_valid) {
    num_instances = blif.getGates().size();
  }
  return is_valid;
}

bool Blif::readBlif(const char* file_name, odb::dbBlock* block)
{
  std::ifstream f(file_name);
  if (f.bad()) {
    logger_->error(CUT, 4, "Cannot open file {}.", file_name);
    return false;
  }

  std::string file_string((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());

  // Remove Comment Lines from Blif
  preprocessString(file_string);

  BlifParser blif;

  bool is_valid = blif.parse(file_string);
  if (!is_valid) {
    logger_->error(CUT,
                   5,
                   "Blif parser failed. File doesn't follow blif spec.",
                   instances_to_optimize_.size());
    return false;
  }

  // Remove and disconnect old instances
  logger_->info(CUT,
                6,
                "Blif parsed successfully, will destroy {} existing instances.",
                instances_to_optimize_.size());
  logger_->info(CUT,
                7,
                "Found {} inputs, {} outputs, {} clocks, {} combinational "
                "gates, {} registers after parsing the blif file.",
                blif.getInputs().size(),
                blif.getOutputs().size(),
                blif.getClocks().size(),
                blif.getCombGateCount(),
                blif.getFlopCount());

  struct Placement
  {
    int x;
    int y;
    odb::dbOrientType orient;
  };
  std::vector<Placement> replacement_locs;
  replacement_locs.reserve(instances_to_optimize_.size());
  std::vector<int> placement_use_count;
  std::map<std::string, std::pair<int, int>> boundary_net_centers;
  const bool place_by_boundary_nets = envIsSet("RMP_PLACE_BY_BOUNDARY_NETS");

  auto inst_center = [](odb::dbInst* inst, int& x, int& y) {
    if (inst == nullptr || inst->getMaster() == nullptr) {
      return false;
    }
    int inst_x = 0;
    int inst_y = 0;
    inst->getLocation(inst_x, inst_y);
    x = inst_x + static_cast<int>(inst->getMaster()->getWidth() / 2);
    y = inst_y + static_cast<int>(inst->getMaster()->getHeight() / 2);
    return true;
  };

  auto collect_boundary_center = [&](odb::dbNet* net, int& cx, int& cy) {
    if (net == nullptr || net->getSigType().isSupply()
        || net->getSigType() == odb::dbSigType::CLOCK) {
      return false;
    }
    int64_t sum_x = 0;
    int64_t sum_y = 0;
    int count = 0;
    for (odb::dbITerm* iterm : net->getITerms()) {
      odb::dbInst* connected_inst = iterm != nullptr ? iterm->getInst()
                                                     : nullptr;
      if (connected_inst == nullptr
          || instances_to_optimize_.find(connected_inst)
                 != instances_to_optimize_.end()) {
        continue;
      }
      int x = 0;
      int y = 0;
      if (!inst_center(connected_inst, x, y)) {
        continue;
      }
      sum_x += x;
      sum_y += y;
      count++;
    }
    if (count == 0) {
      return false;
    }
    cx = static_cast<int>(sum_x / count);
    cy = static_cast<int>(sum_y / count);
    return true;
  };

  for (auto& inst : instances_to_optimize_) {
    int x = 0;
    int y = 0;
    inst->getLocation(x, y);
    replacement_locs.push_back({x, y, inst->getOrient()});

    std::set<odb::dbNet*> connected_nets;
    auto iterms = inst->getITerms();
    for (auto iterm : iterms) {
      auto net = iterm->getNet();
      if (place_by_boundary_nets && net != nullptr) {
        int cx = 0;
        int cy = 0;
        if (collect_boundary_center(net, cx, cy)) {
          boundary_net_centers[net->getName()] = {cx, cy};
        }
      }
      iterm->disconnect();
      if (net && net->getITerms().empty() && net->getBTerms().empty()) {
        odb::dbNet::destroy(net);
      }
    }
    odb::dbInst::destroy(inst);
  }

  // Create and connect new instances
  auto gates = blif.getGates();
  logger_->info(CUT, 8, "Inserting {} new instances.", gates.size());
  std::map<std::string, int> inst_ids;
  size_t placement_idx = 0;
  int snapped_placements = 0;
  int raw_placements = 0;
  int boundary_placements = 0;
  placement_use_count.resize(replacement_locs.size(), 0);

  auto connection_net_name = [](const std::string& connection) {
    const auto equal_sign_pos = connection.find("=");
    if (equal_sign_pos == std::string::npos
        || equal_sign_pos == connection.length() - 1) {
      return std::string();
    }
    return connection.substr(equal_sign_pos + 1);
  };

  auto best_boundary_placement_index =
      [&](const std::vector<std::string>& connections) {
        int64_t best_score = std::numeric_limits<int64_t>::max();
        size_t best_index = replacement_locs.size();
        for (size_t idx = 0; idx < replacement_locs.size(); ++idx) {
          int64_t score = 0;
          int matched_nets = 0;
          for (const std::string& connection : connections) {
            const std::string net_name = connection_net_name(connection);
            const auto center_itr = boundary_net_centers.find(net_name);
            if (center_itr == boundary_net_centers.end()) {
              continue;
            }
            const Placement& placement = replacement_locs[idx];
            score += std::llabs(static_cast<int64_t>(placement.x)
                                - center_itr->second.first);
            score += std::llabs(static_cast<int64_t>(placement.y)
                                - center_itr->second.second);
            matched_nets++;
          }
          if (matched_nets == 0) {
            continue;
          }
          score += static_cast<int64_t>(placement_use_count[idx]) * 1000;
          if (score < best_score) {
            best_score = score;
            best_index = idx;
          }
        }
        return best_index;
      };

  auto place_new_inst = [&](odb::dbInst* new_inst,
                            const std::vector<std::string>& connections) {
    if (new_inst == nullptr || replacement_locs.empty()) {
      return;
    }
    size_t selected_idx = placement_idx % replacement_locs.size();
    if (place_by_boundary_nets) {
      const size_t boundary_idx = best_boundary_placement_index(connections);
      if (boundary_idx < replacement_locs.size()) {
        selected_idx = boundary_idx;
        boundary_placements++;
      }
    }
    const Placement& placement = replacement_locs[selected_idx];
    placement_idx++;
    placement_use_count[selected_idx]++;
    int x = placement.x;
    int y = placement.y;
    odb::dbOrientType orient = placement.orient;
    if (snapToPlacementRow(block, new_inst->getMaster(), x, y, x, y, orient)) {
      snapped_placements++;
    } else {
      raw_placements++;
    }
    new_inst->setOrient(orient);
    new_inst->setLocation(x, y);
    new_inst->setPlacementStatus(odb::dbPlacementStatus::PLACED);
  };

  for (auto&& gate : gates) {
    GateType master_type = gate.type;
    std::string master_name = gate.master;
    std::vector<std::string> connections = gate.connections;
    odb::dbMaster* master = nullptr;

    for (auto&& lib : block->getDb()->getLibs()) {
      master = lib->findMaster(master_name.c_str());
      if (master != nullptr) {
        break;
      }
    }

    if (master == nullptr
        && (master_name == "_const0_" || master_name == "_const1_")) {
      if (connections.empty()) {
        logger_->info(CUT,
                      9,
                      "Const driver {} doesn't have any connected nets.",
                      master_name.c_str());
        continue;
      }
      auto const_net_name = connections[0].substr(connections[0].find('=') + 1);
      odb::dbNet* net = block->findNet(const_net_name.c_str());
      if (net == nullptr) {
        std::string net_name_modified
            = std::string("or_") + std::to_string(call_id_) + const_net_name;
        net = odb::dbNet::create(block, net_name_modified.c_str());
      }

      // Add tie cells
      std::string const_master
          = (master_name == "_const0_") ? const0_cell_ : const1_cell_;
      std::string const_port
          = (master_name == "_const0_") ? const0_cell_port_ : const1_cell_port_;
      inst_ids[const_master]
          = (inst_ids[const_master]) ? inst_ids[const_master] + 1 : 1;
      std::string inst_name = const_master + "_" + std::to_string(call_id_)
                             + std::to_string(inst_ids[const_master]);
      for (auto&& lib : block->getDb()->getLibs()) {
        master = lib->findMaster(const_master.c_str());
        if (master != nullptr) {
          break;
        }
      }

      if (master != nullptr) {
        while (block->findInst(inst_name.c_str())) {
          inst_ids[const_master]++;
          inst_name = const_master + "_" + std::to_string(call_id_)
                     + std::to_string(inst_ids[const_master]);
        }
        auto new_inst = odb::dbInst::create(block, master, inst_name.c_str());
        place_new_inst(new_inst, connections);
        new_inst->findITerm(const_port.c_str())->connect(net);
      }

      continue;
    }

    if (master == nullptr) {
      logger_->info(CUT,
                    10,
                    "Master ({}) not found while stitching back instances.",
                    master_name.c_str());
      // return false;
      continue;
    }

    inst_ids[master_name]
        = (inst_ids[master_name]) ? inst_ids[master_name] + 1 : 1;
    std::string inst_name = master_name + "_" + std::to_string(call_id_) + "_"
                           + std::to_string(inst_ids[master_name]);
    while (block->findInst(inst_name.c_str())) {
      inst_ids[master_name]++;
      inst_name = master_name + "_" + std::to_string(call_id_) + "_"
                 + std::to_string(inst_ids[master_name]);
    }

    auto new_inst = odb::dbInst::create(block, master, inst_name.c_str());
    place_new_inst(new_inst, connections);

    if (new_inst == nullptr) {
      logger_->error(CUT,
                     11,
                     "Could not create new instance of type {} with name {}.",
                     master_name,
                     inst_name);
      continue;
    }

    for (auto&& connection : connections) {
      auto equal_sign_pos = connection.find("=");

      std::string mterm_name, net_name;

      if (equal_sign_pos == std::string::npos
          && master_type == GateType::kMlatch) {
        // Identified clock net!
        // Find clock pin
        auto master_terms = master->getMTerms();
        for (auto&& m_term : master_terms) {
          // Assuming that no more than 1 Pin can have clock type!
          auto pin = open_sta_->getDbNetwork()->dbToSta(m_term);
          auto network = open_sta_->network();
          auto port = network->libertyPort(pin);
          if (port->isClock()) {
            mterm_name = m_term->getName();
            net_name = std::move(connection);
            break;
          }
        }

      } else if (equal_sign_pos == std::string::npos) {
        continue;
      } else {
        if (equal_sign_pos == connection.length() - 1) {
          logger_->info(CUT,
                        12,
                        "Connection {} parsing failed for {} instance.",
                        connection,
                        master_name);
          continue;
        }
        mterm_name = connection.substr(0, equal_sign_pos);
        net_name = connection.substr(equal_sign_pos + 1);
      }

      odb::dbNet* net = block->findNet(net_name.c_str());
      if (net == nullptr) {
        std::string net_name_modified
            = std::string("or_") + std::to_string(call_id_) + net_name;
        net = block->findNet(net_name_modified.c_str());
        if (!net) {
          net = odb::dbNet::create(block, net_name_modified.c_str());
        }
      }

      if (mterm_name.empty()) {
        logger_->info(CUT,
                      13,
                      "Could not connect instance of cell type {} to {} net "
                      "due to unknown mterm in blif.",
                      master_name,
                      net_name);
        continue;
      }

      new_inst->findITerm(mterm_name.c_str())->connect(net);
    }
  }

  if (snapped_placements > 0 || raw_placements > 0) {
    logger_->report("RMP_GUARD|blif_place_snapped|snapped={}|raw={}|"
                    "boundary={}",
                    snapped_placements,
                    raw_placements,
                    boundary_placements);
  }

  return true;
}

float Blif::getRequiredTime(sta::Pin* term, bool is_rise)
{
  auto vert = open_sta_->getDbNetwork()->graph()->pinLoadVertex(term);
  auto req = open_sta_->required(
      vert,
      is_rise ? sta::RiseFallBoth::rise() : sta::RiseFallBoth::fall(),
      open_sta_->scenes(),
      sta::MinMax::max());
  if (sta::delayInf(req, open_sta_)) {
    return 0;
  }
  return req;
}

float Blif::getArrivalTime(sta::Pin* term, bool is_rise)
{
  auto vert = open_sta_->getDbNetwork()->graph()->pinLoadVertex(term);
  auto path = open_sta_->vertexWorstArrivalPath(vert, sta::MinMax::max());
  if (path == nullptr) {
    return 0;
  }
  sta::SceneSeq scene1({path->scene(open_sta_)});
  auto arr = open_sta_->arrival(
      vert,
      is_rise ? sta::RiseFallBoth::rise() : sta::RiseFallBoth::fall(),
      scene1,
      path->minMax(open_sta_));
  if (sta::delayInf(arr, open_sta_)) {
    return 0;
  }
  return arr;
}

void Blif::addArrival(sta::Pin* pin, const std::string& net_name)
{
  if (arrivals_.find(net_name) == arrivals_.end()) {
    arrivals_[net_name] = std::pair<float, float>(
        getArrivalTime(pin, true) * 1e12, getArrivalTime(pin, false) * 1e12);
  }
}

void Blif::addRequired(sta::Pin* pin, const std::string& net_name)
{
  if (requireds_.find(net_name) == requireds_.end()) {
    requireds_[net_name] = std::pair<float, float>(
        getRequiredTime(pin, true) * 1e12, getRequiredTime(pin, false) * 1e12);
  }
}

}  // namespace cut
