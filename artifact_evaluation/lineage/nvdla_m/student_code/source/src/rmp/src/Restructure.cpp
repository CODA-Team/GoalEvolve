// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2019-2026, The OpenROAD Authors

#include "rmp/Restructure.h"

#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cstdlib>
#include <cstring>
#include <ctime>
#include <cmath>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

#include "annealing_strategy.h"
#include "base/main/abcapis.h"
#include "cut/abc_init.h"
#include "cut/blif.h"
#include "db_sta/dbSta.hh"
#include "genetic_strategy.h"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/Delay.hh"
#include "sta/Graph.hh"
#include "sta/GraphClass.hh"
#include "sta/Liberty.hh"
#include "sta/NetworkClass.hh"
#include "sta/Path.hh"
#include "sta/PathEnd.hh"
#include "sta/PathExpanded.hh"
#include "sta/PortDirection.hh"
#include "sta/Sdc.hh"
#include "sta/Search.hh"
#include "sta/Sta.hh"
#include "utl/Logger.h"
#include "zero_slack_strategy.h"

namespace rmp {

using abc::Abc_Frame_t;
using abc::Abc_FrameGetGlobalFrame;
using abc::Abc_Start;
using abc::Abc_Stop;
using cut::Blif;
using utl::RMP;

namespace {

struct SavedConnection
{
  std::string mterm_name;
  std::string net_name;
};

struct SavedInst
{
  std::string name;
  odb::dbMaster* master = nullptr;
  int x = 0;
  int y = 0;
  odb::dbOrientType orient;
  odb::dbPlacementStatus placement_status;
  std::vector<SavedConnection> connections;
};

struct RmpSnapshot
{
  std::map<std::string, SavedInst> insts;
  std::set<std::string> all_inst_names;
  std::set<std::string> all_net_names;
  std::map<std::string, odb::dbSigType> net_sig_types;
};

using SeqPinNetSnapshot = std::map<std::string, std::map<std::string, std::string>>;

sta::Slack envSlackNs(const char* name, const sta::Slack default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr || value[0] == '\0') {
    return default_value;
  }
  return std::atof(value) * 1.0e-9;
}

bool envIsSet(const char* name)
{
  const char* value = std::getenv(name);
  return value != nullptr && value[0] != '\0';
}

double envDouble(const char* name, const double default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr || value[0] == '\0') {
    return default_value;
  }
  return std::atof(value);
}

bool isPathConeHaloSampledRecipe()
{
  return envIsSet("RMP_PATH_CONE_ONLY")
         && envIsSet("RMP_UNION_ENDPOINT_PATHS")
         && envIsSet("RMP_UNIQUE_ENDPOINTS")
         && !envIsSet("RMP_LEGACY_FANIN_BLOB")
         && !envIsSet("RMP_PRUNE_SIDE_OUTPUTS_ALLOW_PATH_REMOVAL")
         && envDouble("RMP_MAX_CLOUDS", 1.0) == 4.0
         && envDouble("RMP_ENDPOINT_PATH_COUNT", 1.0) == 4.0
         && envDouble("RMP_MAX_TRIED_CLOUDS", 0.0) == 4.0
         && envDouble("RMP_MAX_ACCEPTED_CLOUDS", 1.0) == 1.0
         && envDouble("RMP_EXPAND_SIDE_FANIN_LEVELS", 0.0) == 1.0
         && envDouble("RMP_EXPAND_SIDE_FANIN_MAX_ADD", 0.0) == 16.0;
}

std::vector<int> envIntList(const char* name)
{
  std::vector<int> values;
  const char* raw_value = std::getenv(name);
  if (raw_value == nullptr || raw_value[0] == '\0') {
    return values;
  }

  std::stringstream value_stream(raw_value);
  std::string token;
  while (std::getline(value_stream, token, ',')) {
    const int value = std::atoi(token.c_str());
    if (value > 0) {
      values.push_back(value);
    }
  }
  return values;
}

std::set<std::string> envStringSet(const char* name)
{
  std::set<std::string> values;
  const char* raw_value = std::getenv(name);
  if (raw_value == nullptr || raw_value[0] == '\0') {
    return values;
  }

  std::stringstream value_stream(raw_value);
  std::string token;
  while (std::getline(value_stream, token, ',')) {
    token.erase(std::remove_if(token.begin(),
                               token.end(),
                               [](const char ch) {
                                 return ch == ' ' || ch == '\t'
                                        || ch == '\n' || ch == '\r';
                               }),
                token.end());
    if (!token.empty()) {
      values.insert(token);
    }
  }
  return values;
}

RmpSnapshot snapshotRmpState(odb::dbBlock* block,
                             const std::set<odb::dbInst*>& path_insts)
{
  RmpSnapshot snapshot;
  for (odb::dbInst* inst : block->getInsts()) {
    snapshot.all_inst_names.insert(inst->getName());
  }
  for (odb::dbNet* net : block->getNets()) {
    const std::string net_name = net->getName();
    snapshot.all_net_names.insert(net_name);
    snapshot.net_sig_types[net_name] = net->getSigType();
  }

  for (odb::dbInst* inst : path_insts) {
    if (inst == nullptr) {
      continue;
    }
    SavedInst saved;
    saved.name = inst->getName();
    saved.master = inst->getMaster();
    inst->getLocation(saved.x, saved.y);
    saved.orient = inst->getOrient();
    saved.placement_status = inst->getPlacementStatus();
    for (odb::dbITerm* iterm : inst->getITerms()) {
      odb::dbNet* net = iterm->getNet();
      if (net == nullptr || iterm->getMTerm() == nullptr) {
        continue;
      }
      saved.connections.push_back(
          {iterm->getMTerm()->getName(), net->getName()});
      snapshot.net_sig_types[net->getName()] = net->getSigType();
      snapshot.all_net_names.insert(net->getName());
    }
    snapshot.insts[saved.name] = std::move(saved);
  }
  return snapshot;
}

std::set<std::string> cloudInstNames(const std::set<odb::dbInst*>& cloud)
{
  std::set<std::string> names;
  for (odb::dbInst* inst : cloud) {
    if (inst != nullptr) {
      names.insert(inst->getName());
    }
  }
  return names;
}

std::set<odb::dbInst*> resolveCloudInsts(odb::dbBlock* block,
                                         const std::set<std::string>& names,
                                         int& missing_count)
{
  std::set<odb::dbInst*> insts;
  missing_count = 0;
  if (block == nullptr) {
    missing_count = names.size();
    return insts;
  }
  for (const std::string& name : names) {
    odb::dbInst* inst = block->findInst(name.c_str());
    if (inst == nullptr) {
      missing_count++;
      continue;
    }
    insts.insert(inst);
  }
  return insts;
}

void restoreRmpState(odb::dbBlock* block, const RmpSnapshot& snapshot)
{
  std::vector<odb::dbInst*> new_insts;
  for (odb::dbInst* inst : block->getInsts()) {
    if (snapshot.all_inst_names.find(inst->getName())
        == snapshot.all_inst_names.end()) {
      new_insts.push_back(inst);
    }
  }
  for (odb::dbInst* inst : new_insts) {
    odb::dbInst::destroy(inst);
  }

  for (const auto& [inst_name, saved] : snapshot.insts) {
    odb::dbInst* inst = block->findInst(inst_name.c_str());
    if (inst == nullptr && saved.master != nullptr) {
      inst = odb::dbInst::create(block, saved.master, inst_name.c_str());
    }
    if (inst == nullptr) {
      continue;
    }
    inst->setOrient(saved.orient);
    inst->setLocation(saved.x, saved.y);
    inst->setPlacementStatus(saved.placement_status);
    for (odb::dbITerm* iterm : inst->getITerms()) {
      iterm->disconnect();
    }
    for (const SavedConnection& connection : saved.connections) {
      odb::dbITerm* iterm = inst->findITerm(connection.mterm_name.c_str());
      if (iterm == nullptr) {
        continue;
      }
      odb::dbNet* net = block->findNet(connection.net_name.c_str());
      if (net == nullptr) {
        net = odb::dbNet::create(block, connection.net_name.c_str());
        const auto sig_type = snapshot.net_sig_types.find(connection.net_name);
        if (sig_type != snapshot.net_sig_types.end()) {
          net->setSigType(sig_type->second);
        }
      }
      iterm->connect(net);
    }
  }

  std::vector<odb::dbNet*> stale_nets;
  for (odb::dbNet* net : block->getNets()) {
    if (snapshot.all_net_names.find(net->getName())
        != snapshot.all_net_names.end()) {
      continue;
    }
    if (net->getITerms().empty() && net->getBTerms().empty()) {
      stale_nets.push_back(net);
    }
  }
  for (odb::dbNet* net : stale_nets) {
    odb::dbNet::destroy(net);
  }
}

bool isSequentialDbInst(sta::dbSta* open_sta, odb::dbInst* inst)
{
  if (open_sta == nullptr || inst == nullptr || inst->getMaster() == nullptr) {
    return false;
  }
  sta::LibertyCell* cell = open_sta->getDbNetwork()->libertyCell(
      open_sta->getDbNetwork()->dbToSta(inst->getMaster()));
  return cell != nullptr && cell->hasSequentials();
}

SeqPinNetSnapshot snapshotSequentialPinNets(sta::dbSta* open_sta,
                                            odb::dbBlock* block)
{
  SeqPinNetSnapshot snapshot;
  if (open_sta == nullptr || block == nullptr) {
    return snapshot;
  }
  for (odb::dbInst* inst : block->getInsts()) {
    if (!isSequentialDbInst(open_sta, inst)) {
      continue;
    }
    auto& pins = snapshot[inst->getName()];
    for (odb::dbITerm* iterm : inst->getITerms()) {
      if (iterm == nullptr || iterm->getMTerm() == nullptr) {
        continue;
      }
      odb::dbNet* net = iterm->getNet();
      pins[iterm->getMTerm()->getName()]
          = net != nullptr ? net->getName() : std::string();
    }
  }
  return snapshot;
}

int sequentialPinNetDiffCount(const SeqPinNetSnapshot& before,
                              const SeqPinNetSnapshot& after,
                              std::string& sample)
{
  int diff_count = 0;
  for (const auto& [inst_name, before_pins] : before) {
    const auto after_inst_itr = after.find(inst_name);
    if (after_inst_itr == after.end()) {
      diff_count += before_pins.size();
      if (sample.empty()) {
        sample = inst_name + ":missing_inst";
      }
      continue;
    }
    for (const auto& [pin_name, before_net] : before_pins) {
      const auto after_pin_itr = after_inst_itr->second.find(pin_name);
      const std::string after_net = after_pin_itr != after_inst_itr->second.end()
                                        ? after_pin_itr->second
                                        : std::string("<missing_pin>");
      if (before_net != after_net) {
        diff_count++;
        if (sample.empty()) {
          sample = inst_name + "/" + pin_name + ":" + before_net + "->"
                   + after_net;
        }
      }
    }
  }
  return diff_count;
}

struct LocalRmpCost
{
  int inst_count = 0;
  double area = 0.0;
  double leakage = 0.0;
};

struct LocalWireCost
{
  int net_count = 0;
  int64_t hpwl_dbu = 0;
  int64_t max_net_hpwl_dbu = 0;
};

double libertyCellLeakage(sta::LibertyCell* cell)
{
  if (cell == nullptr) {
    return 0.0;
  }
  float leakage = 0.0F;
  bool exists = false;
  cell->leakagePower(leakage, exists);
  if (exists) {
    return leakage;
  }
  const sta::LeakagePowerSeq& leakages = cell->leakagePowers();
  if (leakages.empty()) {
    return 0.0;
  }
  double total_leakage = 0.0;
  for (const sta::LeakagePower& leak : leakages) {
    total_leakage += leak.power();
  }
  return total_leakage / static_cast<double>(leakages.size());
}

LocalRmpCost masterCost(sta::dbSta* open_sta, odb::dbMaster* master)
{
  LocalRmpCost cost;
  if (open_sta == nullptr || master == nullptr) {
    return cost;
  }
  sta::LibertyCell* cell = open_sta->getDbNetwork()->libertyCell(
      open_sta->getDbNetwork()->dbToSta(master));
  cost.inst_count = 1;
  if (cell != nullptr) {
    cost.area = cell->area();
    cost.leakage = libertyCellLeakage(cell);
  } else {
    cost.area = master->getArea();
  }
  return cost;
}

void addLocalCost(LocalRmpCost& total, const LocalRmpCost& add)
{
  total.inst_count += add.inst_count;
  total.area += add.area;
  total.leakage += add.leakage;
}

LocalRmpCost cloudLocalCost(sta::dbSta* open_sta,
                            const std::set<odb::dbInst*>& cloud)
{
  LocalRmpCost cost;
  for (odb::dbInst* inst : cloud) {
    if (inst != nullptr) {
      addLocalCost(cost, masterCost(open_sta, inst->getMaster()));
    }
  }
  return cost;
}

LocalRmpCost snapshotLocalCost(sta::dbSta* open_sta,
                               const RmpSnapshot& snapshot)
{
  LocalRmpCost cost;
  for (const auto& [inst_name, saved] : snapshot.insts) {
    addLocalCost(cost, masterCost(open_sta, saved.master));
  }
  return cost;
}

LocalRmpCost replacementLocalCost(sta::dbSta* open_sta,
                                  odb::dbBlock* block,
                                  const RmpSnapshot& snapshot)
{
  LocalRmpCost cost;
  if (block == nullptr) {
    return cost;
  }
  for (odb::dbInst* inst : block->getInsts()) {
    if (inst == nullptr
        || snapshot.all_inst_names.find(inst->getName())
               != snapshot.all_inst_names.end()) {
      continue;
    }
    addLocalCost(cost, masterCost(open_sta, inst->getMaster()));
  }
  return cost;
}

bool instCenter(odb::dbInst* inst, int& x, int& y)
{
  if (inst == nullptr || inst->getMaster() == nullptr) {
    return false;
  }
  int inst_x = 0;
  int inst_y = 0;
  inst->getLocation(inst_x, inst_y);
  x = inst_x + static_cast<int>(inst->getMaster()->getWidth() / 2);
  y = inst_y + static_cast<int>(inst->getMaster()->getHeight() / 2);
  return true;
}

int64_t netHpwlDbu(odb::dbNet* net)
{
  if (net == nullptr || net->getSigType().isSupply()
      || net->getSigType() == odb::dbSigType::CLOCK) {
    return 0;
  }

  bool have_point = false;
  int min_x = std::numeric_limits<int>::max();
  int min_y = std::numeric_limits<int>::max();
  int max_x = std::numeric_limits<int>::min();
  int max_y = std::numeric_limits<int>::min();
  for (odb::dbITerm* iterm : net->getITerms()) {
    int x = 0;
    int y = 0;
    if (!instCenter(iterm != nullptr ? iterm->getInst() : nullptr, x, y)) {
      continue;
    }
    have_point = true;
    min_x = std::min(min_x, x);
    min_y = std::min(min_y, y);
    max_x = std::max(max_x, x);
    max_y = std::max(max_y, y);
  }
  if (!have_point) {
    return 0;
  }
  return static_cast<int64_t>(max_x - min_x)
         + static_cast<int64_t>(max_y - min_y);
}

void addWireNetCost(LocalWireCost& total, odb::dbNet* net)
{
  const int64_t hpwl = netHpwlDbu(net);
  if (hpwl <= 0) {
    return;
  }
  total.net_count++;
  total.hpwl_dbu += hpwl;
  total.max_net_hpwl_dbu = std::max(total.max_net_hpwl_dbu, hpwl);
}

LocalWireCost cloudWireCost(const std::set<odb::dbInst*>& cloud)
{
  LocalWireCost cost;
  std::set<odb::dbNet*> nets;
  for (odb::dbInst* inst : cloud) {
    if (inst == nullptr) {
      continue;
    }
    for (odb::dbITerm* iterm : inst->getITerms()) {
      if (iterm != nullptr && iterm->getNet() != nullptr) {
        nets.insert(iterm->getNet());
      }
    }
  }
  for (odb::dbNet* net : nets) {
    addWireNetCost(cost, net);
  }
  return cost;
}

LocalWireCost snapshotWireCost(odb::dbBlock* block,
                               const RmpSnapshot& snapshot)
{
  LocalWireCost cost;
  if (block == nullptr) {
    return cost;
  }

  std::set<std::string> net_names;
  for (const auto& [inst_name, saved] : snapshot.insts) {
    for (const SavedConnection& connection : saved.connections) {
      net_names.insert(connection.net_name);
    }
  }
  for (const std::string& net_name : net_names) {
    addWireNetCost(cost, block->findNet(net_name.c_str()));
  }
  return cost;
}

LocalWireCost replacementWireCost(odb::dbBlock* block,
                                  const RmpSnapshot& snapshot)
{
  LocalWireCost cost;
  if (block == nullptr) {
    return cost;
  }

  std::set<odb::dbNet*> nets;
  for (odb::dbInst* inst : block->getInsts()) {
    if (inst == nullptr
        || snapshot.all_inst_names.find(inst->getName())
               != snapshot.all_inst_names.end()) {
      continue;
    }
    for (odb::dbITerm* iterm : inst->getITerms()) {
      if (iterm != nullptr && iterm->getNet() != nullptr) {
        nets.insert(iterm->getNet());
      }
    }
  }
  for (const auto& [inst_name, saved] : snapshot.insts) {
    for (const SavedConnection& connection : saved.connections) {
      odb::dbNet* net = block->findNet(connection.net_name.c_str());
      if (net != nullptr) {
        nets.insert(net);
      }
    }
  }
  for (odb::dbNet* net : nets) {
    addWireNetCost(cost, net);
  }
  return cost;
}

std::string localWireRejectReason(odb::dbBlock* block,
                                  const LocalWireCost& before,
                                  const LocalWireCost& after)
{
  std::string reason;
  const double hpwl_growth_limit
      = envDouble("RMP_GUARD_MAX_LOCAL_HPWL_GROWTH_RATIO", -1.0);
  if (hpwl_growth_limit >= 0.0 && before.hpwl_dbu > 0) {
    const double hpwl_growth
        = (static_cast<double>(after.hpwl_dbu - before.hpwl_dbu))
          / static_cast<double>(before.hpwl_dbu);
    if (hpwl_growth > hpwl_growth_limit) {
      reason += "local_hpwl_growth;";
    }
  }

  const double max_hpwl_growth_limit
      = envDouble("RMP_GUARD_MAX_LOCAL_MAX_NET_HPWL_GROWTH_RATIO", -1.0);
  if (max_hpwl_growth_limit >= 0.0 && before.max_net_hpwl_dbu > 0) {
    const double max_hpwl_growth
        = (static_cast<double>(after.max_net_hpwl_dbu
                               - before.max_net_hpwl_dbu))
          / static_cast<double>(before.max_net_hpwl_dbu);
    if (max_hpwl_growth > max_hpwl_growth_limit) {
      reason += "local_max_net_hpwl_growth;";
    }
  }

  const double max_net_hpwl_um
      = envDouble("RMP_GUARD_MAX_LOCAL_NET_HPWL_UM", -1.0);
  if (max_net_hpwl_um >= 0.0 && block != nullptr
      && block->getDbUnitsPerMicron() > 0) {
    const int64_t limit_dbu = static_cast<int64_t>(
        max_net_hpwl_um * static_cast<double>(block->getDbUnitsPerMicron()));
    if (after.max_net_hpwl_dbu > limit_dbu) {
      reason += "local_max_net_hpwl_abs;";
    }
  }

  const double max_total_hpwl_um
      = envDouble("RMP_GUARD_MAX_LOCAL_TOTAL_HPWL_UM", -1.0);
  if (max_total_hpwl_um >= 0.0 && block != nullptr
      && block->getDbUnitsPerMicron() > 0) {
    const int64_t limit_dbu = static_cast<int64_t>(
        max_total_hpwl_um * static_cast<double>(block->getDbUnitsPerMicron()));
    if (after.hpwl_dbu > limit_dbu) {
      reason += "local_total_hpwl_abs;";
    }
  }

  return reason;
}

std::string localCostRejectReason(const LocalRmpCost& before,
                                  const LocalRmpCost& after,
                                  const sta::Slack before_tns,
                                  const sta::Slack after_tns)
{
  std::string reason;
  const double tns_improve_ns
      = static_cast<double>(after_tns - before_tns) * 1.0e9;
  const double area_growth_limit
      = envDouble("RMP_GUARD_MAX_LOCAL_AREA_GROWTH_RATIO", -1.0);
  double area_growth = 0.0;
  if (area_growth_limit >= 0.0 && before.area > 0.0) {
    area_growth = (after.area - before.area) / before.area;
    if (area_growth > area_growth_limit) {
      reason += "local_area_growth;";
    }
  }

  const double leakage_growth_limit
      = envDouble("RMP_GUARD_MAX_LOCAL_LEAKAGE_GROWTH_RATIO", -1.0);
  double leakage_growth = 0.0;
  if (leakage_growth_limit >= 0.0 && before.leakage > 0.0) {
    leakage_growth = (after.leakage - before.leakage) / before.leakage;
    if (leakage_growth > leakage_growth_limit) {
      reason += "local_leakage_growth;";
    }
  }

  const double leakage_per_tns_limit
      = envDouble("RMP_GUARD_MAX_LOCAL_LEAKAGE_GROWTH_PER_TNS_NS", -1.0);
  if (leakage_per_tns_limit >= 0.0 && before.leakage > 0.0
      && leakage_growth > 0.0) {
    if (tns_improve_ns <= 1.0e-6) {
      reason += "local_leakage_without_tns_gain;";
    } else if (leakage_growth / tns_improve_ns > leakage_per_tns_limit) {
      reason += "local_leakage_per_tns;";
    }
  }

  const double area_per_tns_limit
      = envDouble("RMP_GUARD_MAX_LOCAL_AREA_GROWTH_PER_TNS_NS", -1.0);
  if (area_per_tns_limit >= 0.0 && before.area > 0.0 && area_growth > 0.0) {
    if (tns_improve_ns <= 1.0e-6) {
      reason += "local_area_without_tns_gain;";
    } else if (area_growth / tns_improve_ns > area_per_tns_limit) {
      reason += "local_area_per_tns;";
    }
  }

  const double inst_growth_limit
      = envDouble("RMP_GUARD_MAX_LOCAL_INST_GROWTH_RATIO", -1.0);
  if (inst_growth_limit >= 0.0 && before.inst_count > 0) {
    const double inst_growth
        = (static_cast<double>(after.inst_count - before.inst_count))
          / static_cast<double>(before.inst_count);
    if (inst_growth > inst_growth_limit) {
      reason += "local_inst_growth;";
    }
  }

  const double min_inst_ratio
      = envDouble("RMP_GUARD_MIN_LOCAL_INST_RATIO", -1.0);
  if (min_inst_ratio >= 0.0 && before.inst_count > 0) {
    const double inst_ratio
        = static_cast<double>(after.inst_count)
          / static_cast<double>(before.inst_count);
    const double bypass_tns_gain
        = envDouble("RMP_GUARD_MIN_LOCAL_INST_RATIO_BYPASS_TNS_GAIN_NS",
                    std::numeric_limits<double>::infinity());
    if (inst_ratio < min_inst_ratio && tns_improve_ns < bypass_tns_gain) {
      reason += "local_inst_collapse;";
    }
  }

  return reason;
}

}  // namespace

Restructure::Restructure(utl::Logger* logger,
                         sta::dbSta* open_sta,
                         odb::dbDatabase* db,
                         rsz::Resizer* resizer,
                         est::EstimateParasitics* estimate_parasitics)
{
  logger_ = logger;
  db_ = db;
  open_sta_ = open_sta;
  resizer_ = resizer;
  estimate_parasitics_ = estimate_parasitics;

  cut::abcInit();
}

void Restructure::deleteComponents()
{
}

Restructure::~Restructure()
{
  deleteComponents();
}

void Restructure::reset()
{
  lib_file_names_.clear();
  path_insts_.clear();
  path_inst_clouds_.clear();
  path_inst_cloud_names_.clear();
  path_inst_cloud_target_net_names_.clear();
  path_inst_target_net_name_.clear();
}

void Restructure::resynth(sta::Scene* corner)
{
  ZeroSlackStrategy zero_slack_strategy(corner);
  zero_slack_strategy.OptimizeDesign(
      open_sta_, name_generator_, resizer_, logger_);
}

void Restructure::resynthAnnealing(sta::Scene* corner)
{
  AnnealingStrategy annealing_strategy(corner,
                                       slack_threshold_,
                                       annealing_seed_,
                                       annealing_temp_,
                                       annealing_iters_,
                                       annealing_revert_after_,
                                       annealing_init_ops_);
  annealing_strategy.OptimizeDesign(
      open_sta_, name_generator_, resizer_, logger_);
}

void Restructure::resynthGenetic(sta::Scene* corner)
{
  GeneticStrategy genetic_strategy(corner,
                                   slack_threshold_,
                                   genetic_seed_,
                                   genetic_population_size_,
                                   genetic_mutation_probability_,
                                   genetic_crossover_probability_,
                                   genetic_tournament_size_,
                                   genetic_tournament_probability_,
                                   genetic_iters_,
                                   genetic_init_ops_);
  genetic_strategy.OptimizeDesign(
      open_sta_, name_generator_, resizer_, logger_);
}

void Restructure::run(char* liberty_file_name,
                      float slack_threshold,
                      unsigned max_depth,
                      char* workdir_name,
                      char* abc_logfile)
{
  reset();
  block_ = db_->getChip()->getBlock();
  if (!block_) {
    return;
  }

  logfile_ = abc_logfile;
  sta::Slack worst_slack = slack_threshold;

  std::stringstream lib_stream(liberty_file_name);
  std::string lib_file_name;
  while (std::getline(lib_stream, lib_file_name, ',')) {
    const auto first = lib_file_name.find_first_not_of(" \t\n\r");
    if (first == std::string::npos) {
      continue;
    }
    const auto last = lib_file_name.find_last_not_of(" \t\n\r");
    lib_file_names_.emplace_back(lib_file_name.substr(first, last - first + 1));
  }
  work_dir_name_ = workdir_name;
  work_dir_name_ = work_dir_name_ + "/";

  if (is_area_mode_) {  // Only in area mode
    removeConstCells();
  }

  getBlob(max_depth);

  if (!path_inst_clouds_.empty()) {
    int max_accepted_clouds = 1;
    if (const char* max_accepted_env = std::getenv("RMP_MAX_ACCEPTED_CLOUDS")) {
      max_accepted_clouds = std::max(1, std::atoi(max_accepted_env));
    }
    int max_cloud_insts = 0;
    if (const char* max_cloud_insts_env = std::getenv("RMP_MAX_CLOUD_INSTS")) {
      max_cloud_insts = std::max(0, std::atoi(max_cloud_insts_env));
    }
    int max_cloud_boundary_outputs = 0;
    if (const char* max_boundary_env
        = std::getenv("RMP_MAX_CLOUD_BOUNDARY_OUTPUTS")) {
      max_cloud_boundary_outputs = std::max(0, std::atoi(max_boundary_env));
    }
    int max_tried_clouds = 0;
    if (const char* max_tried_env = std::getenv("RMP_MAX_TRIED_CLOUDS")) {
      max_tried_clouds = std::max(0, std::atoi(max_tried_env));
    }
    int max_tries_per_target_net = 0;
    if (const char* max_tries_env
        = std::getenv("RMP_MAX_TRIES_PER_TARGET_NET")) {
      max_tries_per_target_net = std::max(0, std::atoi(max_tries_env));
    }
    const bool skip_accepted_target_nets
        = envIsSet("RMP_SKIP_ACCEPTED_TARGET_NETS");
    const bool skip_duplicate_clouds = envIsSet("RMP_SKIP_DUPLICATE_CLOUDS");
    const bool skip_small_clouds_below_min
        = envIsSet("RMP_SKIP_SMALL_CLOUDS_BELOW_MIN");
    int min_live_cloud_insts = 0;
    if (const char* min_live_env = std::getenv("RMP_MIN_LIVE_CLOUD_INSTS")) {
      min_live_cloud_insts = std::max(0, std::atoi(min_live_env));
    } else if (const char* prune_min_env
               = std::getenv("RMP_PRUNE_SIDE_OUTPUTS_MIN_INSTS")) {
      min_live_cloud_insts = std::max(0, std::atoi(prune_min_env));
    }
    int accepted_clouds = 0;
    int tried_clouds = 0;
    int skipped_clouds = 0;
    std::map<std::string, int> target_net_try_counts;
    std::set<std::string> accepted_target_nets;
    std::set<std::string> tried_cloud_keys;
    const std::set<std::string> target_net_allowlist
        = envStringSet("RMP_TARGET_NET_ALLOWLIST");
    const bool post_timing_halo_recipe
        = max_accepted_clouds == 1 && max_tried_clouds == 4
          && envIsSet("RMP_UNION_ENDPOINT_PATHS")
          && !envIsSet("RMP_LEGACY_FANIN_BLOB")
          && !envIsSet("RMP_PRUNE_SIDE_OUTPUTS_ALLOW_PATH_REMOVAL")
          && envDouble("RMP_MAX_CLOUDS", 1.0) == 4.0
          && envDouble("RMP_ENDPOINT_PATH_COUNT", 1.0) == 4.0
          && envDouble("RMP_EXPAND_SIDE_FANIN_LEVELS", 0.0) == 2.0
          && envDouble("RMP_EXPAND_SIDE_FANIN_MAX_ADD", 64.0) == 24.0;
    int post_timing_halo_examined = 0;

    auto try_budget_exhausted = [&]() {
      return max_tried_clouds > 0 && tried_clouds >= max_tried_clouds;
    };

    auto try_cloud = [&](const size_t cloud_index) {
      if (accepted_clouds >= max_accepted_clouds) {
        return false;
      }
      if (try_budget_exhausted()) {
        logger_->report("RMP_GUARD|try_budget_exhausted|tried={}|limit={}",
                        tried_clouds,
                        max_tried_clouds);
        return false;
      }
      int missing_count = 0;
      if (cloud_index < path_inst_cloud_names_.size()) {
        path_insts_ = resolveCloudInsts(
            block_, path_inst_cloud_names_[cloud_index], missing_count);
        path_inst_target_net_name_
            = cloud_index < path_inst_cloud_target_net_names_.size()
                  ? path_inst_cloud_target_net_names_[cloud_index]
                  : std::string();
        if (missing_count > 0) {
          logger_->report(
              "RMP_GUARD|resolved_cloud|index={}|missing={}|instances={}",
              tried_clouds,
              missing_count,
              path_insts_.size());
        }
      } else {
        path_insts_ = path_inst_clouds_[cloud_index];
        path_inst_target_net_name_
            = cloud_index < path_inst_cloud_target_net_names_.size()
                  ? path_inst_cloud_target_net_names_[cloud_index]
                  : std::string();
      }
      if (path_insts_.empty()) {
        logger_->report("RMP_GUARD|skip_cloud|index={}|reason=empty_live",
                        tried_clouds);
        skipped_clouds++;
        tried_clouds++;
        return false;
      }
      if (post_timing_halo_recipe && missing_count > 0) {
        logger_->report(
            "RMP_GUARD|skip_cloud|index={}|instances={}|reason=incomplete_"
            "restore|missing={}",
            tried_clouds,
            path_insts_.size(),
            missing_count);
        skipped_clouds++;
        tried_clouds++;
        return false;
      }
      if (!target_net_allowlist.empty()
          && target_net_allowlist.count(path_inst_target_net_name_) == 0) {
        logger_->report(
            "RMP_GUARD|skip_cloud|index={}|instances={}|target_net={}|"
            "reason=target_not_allowlisted",
            tried_clouds,
            path_insts_.size(),
            path_inst_target_net_name_);
        skipped_clouds++;
        tried_clouds++;
        return false;
      }
      if (skip_small_clouds_below_min && min_live_cloud_insts > 0
          && static_cast<int>(path_insts_.size()) < min_live_cloud_insts) {
        logger_->report(
            "RMP_GUARD|skip_cloud|index={}|instances={}|target_net={}|"
            "reason=below_min_live_insts|limit={}",
            tried_clouds,
            path_insts_.size(),
            path_inst_target_net_name_,
            min_live_cloud_insts);
        skipped_clouds++;
        tried_clouds++;
        return false;
      }
      if (skip_accepted_target_nets && !path_inst_target_net_name_.empty()
          && accepted_target_nets.count(path_inst_target_net_name_) != 0) {
        logger_->report(
            "RMP_GUARD|skip_cloud|index={}|instances={}|target_net={}|"
            "reason=accepted_target_net",
            tried_clouds,
            path_insts_.size(),
            path_inst_target_net_name_);
        skipped_clouds++;
        tried_clouds++;
        return false;
      }
      if (max_tries_per_target_net > 0 && !path_inst_target_net_name_.empty()
          && target_net_try_counts[path_inst_target_net_name_]
                 >= max_tries_per_target_net) {
        logger_->report(
            "RMP_GUARD|skip_cloud|index={}|instances={}|target_net={}|"
            "reason=max_tries_per_target|tries={}|limit={}",
            tried_clouds,
            path_insts_.size(),
            path_inst_target_net_name_,
            target_net_try_counts[path_inst_target_net_name_],
            max_tries_per_target_net);
        skipped_clouds++;
        tried_clouds++;
        return false;
      }
      if (skip_duplicate_clouds) {
        std::ostringstream key_stream;
        key_stream << path_inst_target_net_name_ << '|';
        for (const std::string& inst_name : cloudInstNames(path_insts_)) {
          key_stream << inst_name << ',';
        }
        const std::string cloud_key = key_stream.str();
        if (tried_cloud_keys.count(cloud_key) != 0) {
          logger_->report(
              "RMP_GUARD|skip_cloud|index={}|instances={}|target_net={}|"
              "reason=duplicate_cloud",
              tried_clouds,
              path_insts_.size(),
              path_inst_target_net_name_);
          skipped_clouds++;
          tried_clouds++;
          return false;
        }
        tried_cloud_keys.insert(cloud_key);
      }
      if (max_cloud_insts > 0
          && static_cast<int>(path_insts_.size()) > max_cloud_insts) {
        logger_->report(
            "RMP_GUARD|skip_cloud|index={}|instances={}|reason=max_insts|"
            "limit={}",
            tried_clouds,
            path_insts_.size(),
            max_cloud_insts);
        skipped_clouds++;
        tried_clouds++;
        return false;
      }
      if (max_cloud_boundary_outputs > 0) {
        const int boundary_outputs = cloudBoundaryOutputCount(path_insts_);
        if (boundary_outputs > max_cloud_boundary_outputs) {
          logger_->report(
              "RMP_GUARD|skip_cloud|index={}|instances={}|reason=max_"
              "boundary_outputs|boundary_outputs={}|limit={}",
              tried_clouds,
              path_insts_.size(),
              boundary_outputs,
              max_cloud_boundary_outputs);
          skipped_clouds++;
          tried_clouds++;
          return false;
        }
      }
      if (post_timing_halo_recipe) {
        const bool legal_cloud
            = std::all_of(path_insts_.begin(),
                          path_insts_.end(),
                          [this](odb::dbInst* inst) {
                            return safeRestructureInst(inst);
                          });
        if (!legal_cloud) {
          logger_->report(
              "RMP_GUARD|skip_cloud|index={}|instances={}|reason=unsafe_"
              "live_inst",
              tried_clouds,
              path_insts_.size());
          skipped_clouds++;
          tried_clouds++;
          return false;
        }
        post_timing_halo_examined++;
        logger_->report("METRIC|rmp_post_timing_halo_examined|{}",
                        post_timing_halo_examined);
      }
      logger_->report("RMP_GUARD|try_cloud|index={}|instances={}|target_net={}",
                      tried_clouds,
                      path_insts_.size(),
                      path_inst_target_net_name_);
      if (!path_inst_target_net_name_.empty()) {
        target_net_try_counts[path_inst_target_net_name_]++;
      }
      const bool accepted = runABC();
      if (accepted) {
        accepted_clouds++;
        if (!path_inst_target_net_name_.empty()) {
          accepted_target_nets.insert(path_inst_target_net_name_);
        }
      }
      tried_clouds++;
      return accepted;
    };

    if (envIsSet("RMP_RESELECT_AFTER_ACCEPT")) {
      int reselect_passes = 0;
      while (accepted_clouds < max_accepted_clouds
             && !path_inst_clouds_.empty() && !try_budget_exhausted()) {
        bool accepted_this_pass = false;
        for (size_t cloud_index = 0; cloud_index < path_inst_clouds_.size();
             ++cloud_index) {
          if (accepted_clouds >= max_accepted_clouds
              || try_budget_exhausted()) {
            break;
          }
          if (try_cloud(cloud_index)) {
            accepted_this_pass = true;
            break;
          }
        }
        if (!accepted_this_pass || accepted_clouds >= max_accepted_clouds) {
          break;
        }
        reselect_passes++;
        path_insts_.clear();
        path_inst_clouds_.clear();
        path_inst_cloud_names_.clear();
        path_inst_cloud_target_net_names_.clear();
        path_inst_target_net_name_.clear();
        getBlob(max_depth);
        logger_->report("RMP_GUARD|reselect_after_accept|pass={}|clouds={}",
                        reselect_passes,
                        path_inst_clouds_.size());
      }
      logger_->report(
          "RMP_GUARD|reselect_summary|passes={}", reselect_passes);
    } else {
      for (size_t cloud_index = 0; cloud_index < path_inst_clouds_.size();
           ++cloud_index) {
        if (accepted_clouds >= max_accepted_clouds
            || try_budget_exhausted()) {
          break;
        }
        try_cloud(cloud_index);
      }
    }
    logger_->report(
        "RMP_GUARD|cloud_summary|tried={}|accepted={}|skipped={}|limit={}|"
        "try_limit={}",
                    tried_clouds,
                    accepted_clouds,
                    skipped_clouds,
                    max_accepted_clouds,
                    max_tried_clouds);

    postABC(worst_slack);
  } else if (!path_insts_.empty()) {
    runABC();

    postABC(worst_slack);
  }
}

void Restructure::getBlob(unsigned max_depth)
{
  open_sta_->ensureGraph();
  open_sta_->ensureLevelized();
  open_sta_->searchPreamble();

  sta::PinSet ends(open_sta_->getDbNetwork());

  getEndPoints(ends, is_area_mode_, max_depth);
  if (!ends.empty()) {
    if (!is_area_mode_ && envIsSet("RMP_LEGACY_FANIN_BLOB")) {
      if (!path_inst_clouds_.empty() || !path_insts_.empty()) {
        logger_->report(
            "RMP_GUARD|legacy_fanin_blob|discard_path_clouds={}|"
            "discard_path_insts={}",
            path_inst_clouds_.size(),
            path_insts_.size());
      }
      path_inst_clouds_.clear();
      path_inst_cloud_names_.clear();
      path_inst_cloud_target_net_names_.clear();
      path_inst_target_net_name_.clear();
      path_insts_.clear();
    }
    if (!is_area_mode_
        && (!path_insts_.empty() || !path_inst_clouds_.empty())) {
      if (!path_inst_clouds_.empty()) {
        logger_->report("Found {} path clouds for restructuring.",
                        path_inst_clouds_.size());
        return;
      }
      logger_->report("Found {} instances for restructuring.",
                      path_insts_.size());
      return;
    }
    if (!is_area_mode_ && envIsSet("RMP_PATH_CONE_ONLY")) {
      if (isPathConeHaloSampledRecipe()) {
        logger_->report("METRIC|rmp_path_cone_admission_decided|1");
      }
      logger_->report(
          "RMP_GUARD|path_cone_only|status=fallback_blocked|reason=no_"
          "admitted_path_cone");
      return;
    }

    sta::PinSet boundary_points = !is_area_mode_
                                      ? resizer_->findFanins(ends)
                                      : resizer_->findFaninFanouts(ends);
    // fanin_fanouts.insert(ends.begin(), ends.end()); // Add seq cells
    logger_->report("Found {} pins in extracted logic.",
                    boundary_points.size());
    for (const sta::Pin* pin : boundary_points) {
      odb::dbITerm* term = nullptr;
      odb::dbBTerm* port = nullptr;
      odb::dbModITerm* moditerm = nullptr;
      open_sta_->getDbNetwork()->staToDb(pin, term, port, moditerm);
      if (term && !term->getInst()->getMaster()->isBlock()
          && safeRestructureInst(term->getInst())) {
        path_insts_.insert(term->getInst());
      }
    }
    logger_->report("Found {} instances for restructuring.",
                    path_insts_.size());
  }
}

bool Restructure::safeRestructureInst(odb::dbInst* inst) const
{
  if (inst == nullptr) {
    return false;
  }

  sta::dbNetwork* network = open_sta_->getDbNetwork();
  sta::Instance* sta_inst = network->dbToSta(inst);
  if (sta_inst == nullptr || !resizer_->isEditableLogicStdCell(sta_inst)) {
    return false;
  }

  sta::LibertyCell* cell
      = network->libertyCell(network->dbToSta(inst->getMaster()));
  if (cell == nullptr || cell->hasSequentials()
      || (!hasSingleOutput(cell) && !isSupportedMultiOutputCell(cell))) {
    return false;
  }

  if ((!envIsSet("RMP_ALLOW_FF_PIN_TOUCH_WITH_NET_GUARD")
       && touchesSequentialPin(inst))
      || isClockLikeCell(inst)
      || touchesClockNetwork(inst)
      || resizer_->drivesSequentialClockPin(sta_inst)) {
    return false;
  }

  return true;
}

bool Restructure::hasSingleOutput(sta::LibertyCell* cell) const
{
  int output_count = 0;
  sta::LibertyCellPortIterator port_iter(cell);
  while (port_iter.hasNext()) {
    sta::LibertyPort* port = port_iter.next();
    if (port != nullptr && port->direction()->isOutput()) {
      output_count++;
      if (output_count > 1) {
        return false;
      }
    }
  }
  return output_count == 1;
}

bool Restructure::isSupportedMultiOutputCell(sta::LibertyCell* cell) const
{
  if (cell == nullptr) {
    return false;
  }
  if (std::getenv("RMP_ALLOW_MULTI_OUTPUT") == nullptr) {
    return false;
  }
  const std::string name = cell->name();
  return name.rfind("FAx", 0) == 0 || name.rfind("HAx", 0) == 0;
}

bool Restructure::touchesSequentialPin(odb::dbInst* inst) const
{
  for (odb::dbITerm* iterm : inst->getITerms()) {
    odb::dbNet* net = iterm->getNet();
    if (net == nullptr) {
      continue;
    }

    for (odb::dbITerm* connected_iterm : net->getITerms()) {
      odb::dbInst* connected_inst = connected_iterm->getInst();
      if (connected_inst == nullptr || connected_inst == inst) {
        continue;
      }
      sta::LibertyCell* connected_cell = open_sta_->getDbNetwork()->libertyCell(
          open_sta_->getDbNetwork()->dbToSta(connected_inst->getMaster()));
      if (connected_cell != nullptr && connected_cell->hasSequentials()) {
        return true;
      }
    }
  }
  return false;
}

bool Restructure::touchesClockNetwork(odb::dbInst* inst) const
{
  for (odb::dbITerm* iterm : inst->getITerms()) {
    if (iterm->getSigType() == odb::dbSigType::CLOCK) {
      return true;
    }

    odb::dbNet* db_net = iterm->getNet();
    if (db_net != nullptr && db_net->getSigType() == odb::dbSigType::CLOCK) {
      return true;
    }

    sta::Pin* pin = open_sta_->getDbNetwork()->dbToSta(iterm);
    if (pin != nullptr && open_sta_->isClock(pin, open_sta_->cmdMode())) {
      return true;
    }

    sta::Net* net = db_net != nullptr
                        ? open_sta_->getDbNetwork()->dbToSta(db_net)
                        : nullptr;
    if (net != nullptr && open_sta_->isClock(net, open_sta_->cmdMode())) {
      return true;
    }

    sta::LibertyPort* port = open_sta_->network()->libertyPort(pin);
    if (port != nullptr && port->isClock()) {
      return true;
    }
  }
  return false;
}

bool Restructure::isClockLikeCell(odb::dbInst* inst) const
{
  sta::LibertyCell* cell = open_sta_->getDbNetwork()->libertyCell(
      open_sta_->getDbNetwork()->dbToSta(inst->getMaster()));
  if (cell == nullptr) {
    return true;
  }
  if (cell->isClockGate() || cell->isClockCell()) {
    return true;
  }

  const std::string master_name = inst->getMaster()->getName();
  return master_name.find("CLK") != std::string::npos
         || master_name.find("ICG") != std::string::npos
         || master_name.find("CK") != std::string::npos;
}

std::set<std::string> Restructure::cloudBoundaryOutputNetNames(
    const std::set<odb::dbInst*>& cloud) const
{
  std::set<std::string> boundary_nets;
  for (odb::dbInst* inst : cloud) {
    if (inst == nullptr) {
      continue;
    }
    for (odb::dbITerm* iterm : inst->getITerms()) {
      if (iterm == nullptr || iterm->getIoType() != odb::dbIoType::OUTPUT) {
        continue;
      }
      odb::dbNet* net = iterm->getNet();
      if (net == nullptr || net->getSigType() == odb::dbSigType::CLOCK) {
        continue;
      }

      bool leaves_cloud = !net->getBTerms().empty();
      for (odb::dbITerm* sink_iterm : net->getITerms()) {
        if (sink_iterm == nullptr
            || sink_iterm->getIoType() != odb::dbIoType::INPUT) {
          continue;
        }
        odb::dbInst* sink_inst = sink_iterm->getInst();
        if (sink_inst != nullptr && cloud.count(sink_inst) == 0) {
          leaves_cloud = true;
          break;
        }
      }
      if (leaves_cloud) {
        boundary_nets.insert(net->getName());
      }
    }
  }
  return boundary_nets;
}

int Restructure::cloudBoundaryOutputCount(
    const std::set<odb::dbInst*>& cloud) const
{
  return cloudBoundaryOutputNetNames(cloud).size();
}

bool Restructure::runABC()
{
  const std::string prefix
      = work_dir_name_ + std::string(block_->getConstName());
  input_blif_file_name_ = prefix + "_crit_path.blif";
  std::vector<std::string> files_to_remove;
  const auto cleanup_files = [&]() {
    for (const auto& file_to_remove : files_to_remove) {
      const bool keep_abc_files = std::getenv("RMP_KEEP_ABC_FILES") != nullptr;
      if (!logger_->debugCheck(RMP, "remap", 1) && !keep_abc_files) {
        std::error_code err;
        if (std::filesystem::remove(file_to_remove, err); err) {
          logger_->error(RMP, 11, "Fail to remove file {}", file_to_remove);
        }
      } else if (keep_abc_files) {
        logger_->report("RMP_KEEP_ABC_FILES|{}", file_to_remove);
      }
    }
  };

  debugPrint(logger_,
             utl::RMP,
             "remap",
             1,
             "Constants before remap {}",
             countConsts(block_));

  Blif blif(
      logger_, open_sta_, locell_, loport_, hicell_, hiport_, ++blif_call_id_);
  blif.setReplaceableInstances(path_insts_);
  if (envIsSet("RMP_WRITE_TARGET_ONLY_OUTPUTS")) {
    if (path_inst_target_net_name_.empty()) {
      logger_->report(
          "RMP_GUARD|reject_target_only_blif|reason=missing_target_net|"
          "instances={}",
          path_insts_.size());
      cleanup_files();
      return false;
    }
    blif.setAllowedOutputNets({path_inst_target_net_name_});
    logger_->report("RMP_GUARD|blif_target_only_outputs|target_net={}",
                    path_inst_target_net_name_);
  }
  const std::set<std::string> run_path_inst_names = cloudInstNames(path_insts_);
  bool has_supported_multi_output = false;
  for (odb::dbInst* inst : path_insts_) {
    if (inst == nullptr) {
      continue;
    }
    sta::LibertyCell* cell = open_sta_->getDbNetwork()->libertyCell(
        open_sta_->getDbNetwork()->dbToSta(inst->getMaster()));
    if (isSupportedMultiOutputCell(cell)) {
      has_supported_multi_output = true;
      break;
    }
  }
  const bool allow_safe_multi_output_names
      = envIsSet("RMP_ALLOW_SAFE_MULTI_OUTPUT_NAMES");
  if (has_supported_multi_output && !allow_safe_multi_output_names
      && !envIsSet("RMP_UNSAFE_MULTI_OUTPUT_ABC")) {
    logger_->report(
        "RMP_GUARD|reject_multi_output_cloud|instances={}|reason=abc_blif_"
        "multi_output_unsafe",
        path_insts_.size());
    cleanup_files();
    return false;
  }
  if (has_supported_multi_output && allow_safe_multi_output_names) {
    logger_->report(
        "RMP_GUARD|allow_safe_multi_output_names|instances={}",
        path_insts_.size());
  }
  const bool write_timing_hints
      = !is_area_mode_
        && (!has_supported_multi_output
            || envIsSet("RMP_WRITE_TIMING_HINTS_FOR_MULTI_OUTPUT"));
  if (!write_timing_hints && !is_area_mode_ && has_supported_multi_output) {
    logger_->report(
        "RMP_GUARD|skip_timing_hints_for_multi_output_names|instances={}",
        path_insts_.size());
  } else if (write_timing_hints && !is_area_mode_ && has_supported_multi_output) {
    logger_->report(
        "RMP_GUARD|write_timing_hints_for_multi_output_names|instances={}",
        path_insts_.size());
  }
  if (!blif.writeBlif(input_blif_file_name_.c_str(), write_timing_hints)) {
    logger_->report("RMP_GUARD|blif_write_failed|instances={}",
                    path_insts_.size());
    cleanup_files();
    return false;
  }
  debugPrint(
      logger_, RMP, "remap", 1, "Writing blif file {}", input_blif_file_name_);
  files_to_remove.emplace_back(input_blif_file_name_);

  // abc optimization
  std::vector<Mode> modes;
  std::vector<pid_t> child_proc;

  if (is_area_mode_) {
    // Area Mode
    modes = {Mode::kArea1, Mode::kArea2, Mode::kArea3};
  } else {
    // Delay Mode
    modes = {Mode::kDelay1, Mode::kDelay2, Mode::kDelay3, Mode::kDelay4};
  }

  child_proc.resize(modes.size(), 0);

  struct AbcCandidate
  {
    std::string blif_name;
    int mode_index = 0;
    int num_instances = 0;
    int level_gain = 0;
    float delay = std::numeric_limits<float>::max();
    bool valid_delay = false;
  };
  std::vector<AbcCandidate> abc_candidates;

  std::string best_blif;
  int best_inst_count = std::numeric_limits<int>::max();
  float best_delay_gain = std::numeric_limits<float>::max();
  int best_level_gain = 0;
  int forced_fallback_mode = -1;
  if (const char* forced_mode = std::getenv("RMP_FALLBACK_MODE")) {
    forced_fallback_mode = std::atoi(forced_mode);
  }
  const bool allow_invalid_abc_metric_fallback
      = envIsSet("RMP_ALLOW_INVALID_ABC_METRIC_FALLBACK");

  debugPrint(
      logger_, RMP, "remap", 1, "Running ABC with {} modes.", modes.size());

  for (size_t curr_mode_idx = 0; curr_mode_idx < modes.size();
       curr_mode_idx++) {
    output_blif_file_name_
        = prefix + std::to_string(curr_mode_idx) + "_crit_path_out.blif";

    opt_mode_ = modes[curr_mode_idx];

    const std::string abc_script_file
        = prefix + std::to_string(curr_mode_idx) + "ord_abc_script.tcl";
    if (logfile_.empty()) {
      logfile_ = prefix + "abc.log";
    }

    debugPrint(logger_,
               RMP,
               "remap",
               1,
               "Writing ABC script file {}.",
               abc_script_file);

    if (writeAbcScript(abc_script_file)) {
      // call linked abc
      Abc_Start();
      Abc_Frame_t* abc_frame = Abc_FrameGetGlobalFrame();
      const std::string command = "source " + abc_script_file;
      child_proc[curr_mode_idx]
          = Cmd_CommandExecute(abc_frame, command.c_str());
      if (child_proc[curr_mode_idx]) {
        logger_->report("RMP_GUARD|abc_failed|command={}", command);
        Abc_Stop();
        cleanup_files();
        return false;
      }
      Abc_Stop();
      // exit linked abc
      files_to_remove.emplace_back(abc_script_file);
    }
  }  // end modes

  // Inspect ABC results to choose blif with least instance count
  for (int curr_mode_idx = 0; curr_mode_idx < modes.size(); curr_mode_idx++) {
    // Skip failed ABC runs
    if (child_proc[curr_mode_idx] != 0) {
      continue;
    }

    output_blif_file_name_
        = prefix + std::to_string(curr_mode_idx) + "_crit_path_out.blif";
    const std::string abc_log_name = logfile_ + std::to_string(curr_mode_idx);

    int level_gain = 0;
    float delay = std::numeric_limits<float>::max();
    int num_instances = 0;
    bool success = readAbcLog(abc_log_name, level_gain, delay);
    if (success) {
      success = blif.inspectBlif(output_blif_file_name_.c_str(), num_instances);
      logger_->report(
          "Optimized to {} instances in iteration {} with max path depth "
          "decrease of {}, delay of {}.",
          num_instances,
          curr_mode_idx,
          level_gain,
          delay);

      if (success) {
        if (!is_area_mode_) {
          abc_candidates.push_back({output_blif_file_name_,
                                    curr_mode_idx,
                                    num_instances,
                                    level_gain,
                                    delay,
                                    delay
                                        < std::numeric_limits<float>::max()
                                              / 4.0F});
        }
        if (is_area_mode_) {
          if (num_instances < best_inst_count) {
            best_inst_count = num_instances;
            best_blif = output_blif_file_name_;
          }
        } else {
          // Using only DELAY_4 for delay based gain since other modes not
          // showing good gains
          const bool has_valid_delay
              = delay < std::numeric_limits<float>::max() / 4.0F;
          if (forced_fallback_mode == curr_mode_idx) {
            logger_->report(
                "RMP_GUARD|forced_abc_mode|mode={}|level_gain={}|delay={}",
                forced_fallback_mode,
                level_gain,
                delay);
            best_delay_gain = delay;
            best_level_gain = level_gain;
            best_blif = output_blif_file_name_;
          } else if (modes[curr_mode_idx] == Mode::kDelay4
                     && forced_fallback_mode < 0) {
            if (level_gain > 0 && has_valid_delay) {
              best_delay_gain = delay;
              best_level_gain = level_gain;
              best_blif = output_blif_file_name_;
            } else if (best_blif.empty() && allow_invalid_abc_metric_fallback) {
              logger_->report(
                  "RMP_GUARD|abc_metric_fallback|level_gain={}|delay={}",
                  level_gain,
                  delay);
              best_delay_gain = delay;
              best_level_gain = level_gain;
              best_blif = output_blif_file_name_;
            } else if (best_blif.empty()) {
              logger_->report(
                  "RMP_GUARD|abc_metric_reject|level_gain={}|delay={}|"
                  "reason=invalid_timing_metric",
                  level_gain,
                  delay);
            }
          }
        }
      }
    }
    files_to_remove.emplace_back(output_blif_file_name_);
  }

  auto rejectReason = [&](const sta::Slack before_tns,
                          const sta::Slack before_wns,
                          const sta::Slack after_tns,
                          const sta::Slack after_wns) {
    const sta::Slack tns_worsen_tol
        = envSlackNs("RMP_GUARD_TNS_WORSEN_NS", 0.02e-9);
    const sta::Slack wns_worsen_tol
        = envSlackNs("RMP_GUARD_WNS_WORSEN_NS", 0.01e-9);
    std::string reject_reason;
    if (after_tns < before_tns - tns_worsen_tol) {
      reject_reason += "tns_worse;";
    }
    if (after_wns < before_wns - wns_worsen_tol) {
      reject_reason += "wns_worse;";
    }
    if (envIsSet("RMP_GUARD_MIN_TNS_IMPROVE_NS")) {
      const sta::Slack min_tns_improve
          = envSlackNs("RMP_GUARD_MIN_TNS_IMPROVE_NS", 0.0);
      if (after_tns < before_tns + min_tns_improve) {
        reject_reason += "tns_not_improved;";
      }
    }
    if (envIsSet("RMP_GUARD_MIN_WNS_IMPROVE_NS")) {
      const sta::Slack min_wns_improve
          = envSlackNs("RMP_GUARD_MIN_WNS_IMPROVE_NS", 0.0);
      if (after_wns < before_wns + min_wns_improve) {
        reject_reason += "wns_not_improved;";
      }
    }
    return reject_reason;
  };

  if (!is_area_mode_ && envIsSet("RMP_STA_SELECT_BEST_MODE")
      && !abc_candidates.empty()) {
    estimate_parasitics_->estimateWireParasitics();
    const sta::Slack before_tns
        = open_sta_->totalNegativeSlack(sta::MinMax::max());
    const sta::Slack before_wns = open_sta_->worstSlack(sta::MinMax::max());
    const RmpSnapshot snapshot = snapshotRmpState(block_, path_insts_);
    const SeqPinNetSnapshot seq_pin_snapshot
        = snapshotSequentialPinNets(open_sta_, block_);
    const LocalRmpCost before_local_cost
        = snapshotLocalCost(open_sta_, snapshot);
    const LocalWireCost before_wire_cost = snapshotWireCost(block_, snapshot);

    const AbcCandidate* best_candidate = nullptr;
    sta::Slack best_after_tns = -sta::INF;
    sta::Slack best_after_wns = -sta::INF;
    LocalRmpCost best_after_local_cost;
    LocalWireCost best_after_wire_cost;
    for (const AbcCandidate& candidate : abc_candidates) {
      restoreRmpState(block_, snapshot);
      estimate_parasitics_->estimateWireParasitics();
      int missing_count = 0;
      path_insts_ = resolveCloudInsts(block_, run_path_inst_names, missing_count);
      blif.setReplaceableInstances(path_insts_);
      if (missing_count > 0 || path_insts_.empty()) {
        logger_->report(
            "RMP_GUARD|sta_trial_resolve_failed|mode={}|missing={}|"
            "instances={}",
            candidate.mode_index,
            missing_count,
            path_insts_.size());
        continue;
      }
      const bool read_ok = blif.readBlif(candidate.blif_name.c_str(), block_);
      if (!read_ok) {
        logger_->report("RMP_GUARD|sta_trial_read_failed|mode={}",
                        candidate.mode_index);
        continue;
      }

      estimate_parasitics_->estimateWireParasitics();
      const sta::Slack after_tns
          = open_sta_->totalNegativeSlack(sta::MinMax::max());
      const sta::Slack after_wns = open_sta_->worstSlack(sta::MinMax::max());
      std::string reject_reason
          = rejectReason(before_tns, before_wns, after_tns, after_wns);
      const LocalRmpCost after_local_cost
          = replacementLocalCost(open_sta_, block_, snapshot);
      const LocalWireCost after_wire_cost
          = replacementWireCost(block_, snapshot);
      reject_reason += localCostRejectReason(before_local_cost,
                                             after_local_cost,
                                             before_tns,
                                             after_tns);
      reject_reason += localWireRejectReason(block_,
                                             before_wire_cost,
                                             after_wire_cost);
      std::string seq_pin_diff_sample;
      const int seq_pin_diff_count = sequentialPinNetDiffCount(
          seq_pin_snapshot,
          snapshotSequentialPinNets(open_sta_, block_),
          seq_pin_diff_sample);
      if (seq_pin_diff_count > 0) {
        reject_reason += "ff_pin_net_changed;";
      }
      logger_->report(
          "RMP_GUARD|sta_trial|mode={}|instances={}|level_gain={}|delay={}|"
          "before_tns={}|after_tns={}|before_wns={}|after_wns={}|"
          "local_area_before={}|local_area_after={}|local_leakage_before={}|"
          "local_leakage_after={}|local_insts_before={}|local_insts_after={}|"
          "local_hpwl_before={}|local_hpwl_after={}|local_max_hpwl_before={}|"
          "local_max_hpwl_after={}|local_wire_nets_before={}|"
          "local_wire_nets_after={}|"
          "ff_pin_net_diffs={}|ff_pin_sample={}|reason={}",
          candidate.mode_index,
          candidate.num_instances,
          candidate.level_gain,
          candidate.delay,
          before_tns,
          after_tns,
          before_wns,
          after_wns,
          before_local_cost.area,
          after_local_cost.area,
          before_local_cost.leakage,
          after_local_cost.leakage,
          before_local_cost.inst_count,
          after_local_cost.inst_count,
          before_wire_cost.hpwl_dbu,
          after_wire_cost.hpwl_dbu,
          before_wire_cost.max_net_hpwl_dbu,
          after_wire_cost.max_net_hpwl_dbu,
          before_wire_cost.net_count,
          after_wire_cost.net_count,
          seq_pin_diff_count,
          seq_pin_diff_sample,
          reject_reason);
      if (!reject_reason.empty()) {
        continue;
      }
      if (best_candidate == nullptr || after_tns > best_after_tns
          || (after_tns == best_after_tns && after_wns > best_after_wns)) {
        best_candidate = &candidate;
        best_after_tns = after_tns;
        best_after_wns = after_wns;
        best_after_local_cost = after_local_cost;
        best_after_wire_cost = after_wire_cost;
      }
    }

    restoreRmpState(block_, snapshot);
    estimate_parasitics_->estimateWireParasitics();
    if (best_candidate != nullptr) {
      int missing_count = 0;
      path_insts_ = resolveCloudInsts(block_, run_path_inst_names, missing_count);
      blif.setReplaceableInstances(path_insts_);
      if (missing_count > 0 || path_insts_.empty()) {
        logger_->report(
            "RMP_GUARD|sta_selected_resolve_failed|mode={}|missing={}|"
            "instances={}",
            best_candidate->mode_index,
            missing_count,
            path_insts_.size());
        cleanup_files();
        return false;
      }
      const bool read_ok = blif.readBlif(best_candidate->blif_name.c_str(),
                                         block_);
      if (read_ok) {
        estimate_parasitics_->estimateWireParasitics();
        logger_->report(
            "RMP_GUARD|accept_sta_selected|mode={}|before_tns={}|after_tns={}|"
            "before_wns={}|after_wns={}|local_area_before={}|"
            "local_area_after={}|local_leakage_before={}|"
            "local_leakage_after={}|local_insts_before={}|local_insts_after={}|"
            "local_hpwl_before={}|local_hpwl_after={}|"
            "local_max_hpwl_before={}|local_max_hpwl_after={}|"
            "local_wire_nets_before={}|local_wire_nets_after={}",
            best_candidate->mode_index,
            before_tns,
            best_after_tns,
            before_wns,
            best_after_wns,
            before_local_cost.area,
            best_after_local_cost.area,
            before_local_cost.leakage,
            best_after_local_cost.leakage,
            before_local_cost.inst_count,
            best_after_local_cost.inst_count,
            before_wire_cost.hpwl_dbu,
            best_after_wire_cost.hpwl_dbu,
            before_wire_cost.max_net_hpwl_dbu,
            best_after_wire_cost.max_net_hpwl_dbu,
            before_wire_cost.net_count,
            best_after_wire_cost.net_count);
        debugPrint(logger_,
                   utl::RMP,
                   "remap",
                   1,
                   "Number constants after restructure {}.",
                   countConsts(block_));
        cleanup_files();
        return true;
      }
    }
    logger_->report(
        "RMP_GUARD|sta_select_no_accept|candidates={}|before_tns={}|"
        "before_wns={}",
        abc_candidates.size(),
        before_tns,
        before_wns);
    cleanup_files();
    return false;
  }

  if (best_inst_count < std::numeric_limits<int>::max() || !best_blif.empty()) {
    estimate_parasitics_->estimateWireParasitics();
    const sta::Slack before_tns
        = open_sta_->totalNegativeSlack(sta::MinMax::max());
    const sta::Slack before_wns = open_sta_->worstSlack(sta::MinMax::max());
    const RmpSnapshot snapshot = snapshotRmpState(block_, path_insts_);
    const SeqPinNetSnapshot seq_pin_snapshot
        = snapshotSequentialPinNets(open_sta_, block_);
    const LocalRmpCost before_local_cost
        = snapshotLocalCost(open_sta_, snapshot);
    const LocalWireCost before_wire_cost = snapshotWireCost(block_, snapshot);

    // read back netlist
    debugPrint(logger_, RMP, "remap", 1, "Reading blif file {}.", best_blif);
    const bool read_ok = blif.readBlif(best_blif.c_str(), block_);
    if (read_ok) {
      estimate_parasitics_->estimateWireParasitics();
      const sta::Slack after_tns
          = open_sta_->totalNegativeSlack(sta::MinMax::max());
      const sta::Slack after_wns = open_sta_->worstSlack(sta::MinMax::max());
      std::string reject_reason
          = rejectReason(before_tns, before_wns, after_tns, after_wns);
      const LocalRmpCost after_local_cost
          = replacementLocalCost(open_sta_, block_, snapshot);
      const LocalWireCost after_wire_cost
          = replacementWireCost(block_, snapshot);
      reject_reason += localCostRejectReason(before_local_cost,
                                             after_local_cost,
                                             before_tns,
                                             after_tns);
      reject_reason += localWireRejectReason(block_,
                                             before_wire_cost,
                                             after_wire_cost);
      std::string seq_pin_diff_sample;
      const int seq_pin_diff_count = sequentialPinNetDiffCount(
          seq_pin_snapshot,
          snapshotSequentialPinNets(open_sta_, block_),
          seq_pin_diff_sample);
      if (seq_pin_diff_count > 0) {
        reject_reason += "ff_pin_net_changed;";
      }
      if (!reject_reason.empty()) {
        logger_->report(
            "RMP_GUARD|reject|before_tns={}|after_tns={}|before_wns={}|"
            "after_wns={}|local_area_before={}|local_area_after={}|"
            "local_leakage_before={}|local_leakage_after={}|"
            "local_insts_before={}|local_insts_after={}|ff_pin_net_diffs={}|"
            "local_hpwl_before={}|local_hpwl_after={}|"
            "local_max_hpwl_before={}|local_max_hpwl_after={}|"
            "local_wire_nets_before={}|local_wire_nets_after={}|"
            "ff_pin_sample={}|reason={}",
            before_tns,
            after_tns,
            before_wns,
            after_wns,
            before_local_cost.area,
            after_local_cost.area,
            before_local_cost.leakage,
            after_local_cost.leakage,
            before_local_cost.inst_count,
            after_local_cost.inst_count,
            seq_pin_diff_count,
            before_wire_cost.hpwl_dbu,
            after_wire_cost.hpwl_dbu,
            before_wire_cost.max_net_hpwl_dbu,
            after_wire_cost.max_net_hpwl_dbu,
            before_wire_cost.net_count,
            after_wire_cost.net_count,
            seq_pin_diff_sample,
            reject_reason);
        restoreRmpState(block_, snapshot);
        estimate_parasitics_->estimateWireParasitics();
      } else {
        logger_->report(
            "RMP_GUARD|accept|before_tns={}|after_tns={}|before_wns={}|"
            "after_wns={}|local_area_before={}|local_area_after={}|"
            "local_leakage_before={}|local_leakage_after={}|"
            "local_insts_before={}|local_insts_after={}|"
            "local_hpwl_before={}|local_hpwl_after={}|"
            "local_max_hpwl_before={}|local_max_hpwl_after={}|"
            "local_wire_nets_before={}|local_wire_nets_after={}",
            before_tns,
            after_tns,
            before_wns,
            after_wns,
            before_local_cost.area,
            after_local_cost.area,
            before_local_cost.leakage,
            after_local_cost.leakage,
            before_local_cost.inst_count,
            after_local_cost.inst_count,
            before_wire_cost.hpwl_dbu,
            after_wire_cost.hpwl_dbu,
            before_wire_cost.max_net_hpwl_dbu,
            after_wire_cost.max_net_hpwl_dbu,
            before_wire_cost.net_count,
            after_wire_cost.net_count);
        debugPrint(logger_,
                   utl::RMP,
                   "remap",
                   1,
                   "Number constants after restructure {}.",
                   countConsts(block_));
        cleanup_files();
        return true;
      }
    }
  } else {
    logger_->info(RMP,
                  4,
                  "All re-synthesis runs discarded, keeping original netlist. "
                  "best_level_gain={} best_delay={}",
                  best_level_gain,
                  best_delay_gain);
  }

  cleanup_files();
  return false;
}

void Restructure::postABC(float worst_slack)
{
  // Leave the parasitics up to date.
  estimate_parasitics_->estimateWireParasitics();
}
void Restructure::getEndPoints(sta::PinSet& ends,
                               bool area_mode,
                               unsigned max_depth)
{
  auto sta_state = open_sta_->search();
  if (!is_area_mode_) {
    int skip_paths = 0;
    if (const char* skip_paths_env = std::getenv("RMP_SKIP_PATHS")) {
      skip_paths = std::max(0, std::atoi(skip_paths_env));
    }
    int max_path_insts = 0;
    if (const char* max_path_insts_env = std::getenv("RMP_MAX_PATH_INSTS")) {
      max_path_insts = std::max(0, std::atoi(max_path_insts_env));
    }
    int max_clouds = 1;
    if (const char* max_clouds_env = std::getenv("RMP_MAX_CLOUDS")) {
      max_clouds = std::max(1, std::atoi(max_clouds_env));
    }
    int endpoint_path_count = 1;
    if (const char* endpoint_path_count_env
        = std::getenv("RMP_ENDPOINT_PATH_COUNT")) {
      endpoint_path_count = std::max(1, std::atoi(endpoint_path_count_env));
    }
    const bool union_endpoint_paths
        = std::getenv("RMP_UNION_ENDPOINT_PATHS") != nullptr;
    const bool unique_endpoints
        = std::getenv("RMP_UNIQUE_ENDPOINTS") != nullptr;
    const bool path_cone_halo_sampled_recipe
        = isPathConeHaloSampledRecipe();
    const std::vector<int> suffix_sweep_sizes
        = envIntList("RMP_SUFFIX_SWEEP_PATH_INSTS");
    int expand_side_fanout_levels = 0;
    if (const char* expand_env = std::getenv("RMP_EXPAND_SIDE_FANOUT_LEVELS")) {
      expand_side_fanout_levels = std::max(0, std::atoi(expand_env));
    }
    int expand_side_fanout_max_add = 64;
    if (const char* expand_max_env
        = std::getenv("RMP_EXPAND_SIDE_FANOUT_MAX_ADD")) {
      expand_side_fanout_max_add = std::max(0, std::atoi(expand_max_env));
    }
    int expand_side_fanin_levels = 0;
    if (const char* expand_env = std::getenv("RMP_EXPAND_SIDE_FANIN_LEVELS")) {
      expand_side_fanin_levels = std::max(0, std::atoi(expand_env));
    }
    int expand_side_fanin_max_add = 64;
    if (const char* expand_max_env
        = std::getenv("RMP_EXPAND_SIDE_FANIN_MAX_ADD")) {
      expand_side_fanin_max_add = std::max(0, std::atoi(expand_max_env));
    }
    const bool prune_side_outputs = envIsSet("RMP_PRUNE_SIDE_OUTPUTS");
    int prune_side_output_target = 0;
    if (const char* prune_target_env
        = std::getenv("RMP_TARGET_CLOUD_BOUNDARY_OUTPUTS")) {
      prune_side_output_target = std::max(0, std::atoi(prune_target_env));
    } else if (const char* max_boundary_env
               = std::getenv("RMP_MAX_CLOUD_BOUNDARY_OUTPUTS")) {
      prune_side_output_target = std::max(0, std::atoi(max_boundary_env));
    } else if (prune_side_outputs) {
      prune_side_output_target = 6;
    }
    int prune_side_output_max_remove = 64;
    if (const char* prune_max_env
        = std::getenv("RMP_PRUNE_SIDE_OUTPUTS_MAX_REMOVE")) {
      prune_side_output_max_remove = std::max(0, std::atoi(prune_max_env));
    }
    const bool prune_side_outputs_allow_path_removal
        = envIsSet("RMP_PRUNE_SIDE_OUTPUTS_ALLOW_PATH_REMOVAL");
    const bool prune_side_outputs_force_shrink
        = envIsSet("RMP_PRUNE_SIDE_OUTPUTS_FORCE_SHRINK");
    int prune_side_output_min_insts = 4;
    if (const char* prune_min_env
        = std::getenv("RMP_PRUNE_SIDE_OUTPUTS_MIN_INSTS")) {
      prune_side_output_min_insts = std::max(1, std::atoi(prune_min_env));
    }
    const int group_path_count
        = std::max(512, max_clouds * endpoint_path_count * 4);
    const bool unique_path_pins = endpoint_path_count <= 1;
    auto expand_side_fanin_cloud = [&](std::set<odb::dbInst*>& cloud) {
      if (expand_side_fanin_levels <= 0 || expand_side_fanin_max_add <= 0
          || cloud.empty()) {
        return;
      }

      std::set<odb::dbInst*> frontier = cloud;
      int added = 0;
      for (int level = 0; level < expand_side_fanin_levels; ++level) {
        std::set<odb::dbInst*> next_frontier;
        for (odb::dbInst* inst : frontier) {
          if (inst == nullptr) {
            continue;
          }
          for (odb::dbITerm* iterm : inst->getITerms()) {
            if (iterm == nullptr
                || iterm->getIoType() != odb::dbIoType::INPUT) {
              continue;
            }
            odb::dbNet* net = iterm->getNet();
            if (net == nullptr || net->getSigType() == odb::dbSigType::CLOCK) {
              continue;
            }
            for (odb::dbITerm* driver_iterm : net->getITerms()) {
              if (driver_iterm == nullptr
                  || driver_iterm->getIoType() != odb::dbIoType::OUTPUT) {
                continue;
              }
              odb::dbInst* driver_inst = driver_iterm->getInst();
              if (driver_inst == nullptr || cloud.count(driver_inst) != 0) {
                continue;
              }
              if (driver_inst->getMaster()->isBlock()
                  || !safeRestructureInst(driver_inst)) {
                continue;
              }
              cloud.insert(driver_inst);
              next_frontier.insert(driver_inst);
              added++;
              if (added >= expand_side_fanin_max_add) {
                logger_->report(
                    "RMP_GUARD|expanded_side_fanin|levels={}|added={}|"
                    "instances={}|hit_limit=1",
                    expand_side_fanin_levels,
                    added,
                    cloud.size());
                return;
              }
            }
          }
        }
        if (next_frontier.empty()) {
          break;
        }
        frontier = std::move(next_frontier);
      }
      logger_->report(
          "RMP_GUARD|expanded_side_fanin|levels={}|added={}|instances={}|"
          "hit_limit=0",
          expand_side_fanin_levels,
          added,
          cloud.size());
    };
    auto expand_side_fanout_cloud = [&](std::set<odb::dbInst*>& cloud) {
      if (expand_side_fanout_levels <= 0 || expand_side_fanout_max_add <= 0
          || cloud.empty()) {
        return;
      }

      std::set<odb::dbInst*> frontier = cloud;
      int added = 0;
      for (int level = 0; level < expand_side_fanout_levels; ++level) {
        std::set<odb::dbInst*> next_frontier;
        for (odb::dbInst* inst : frontier) {
          if (inst == nullptr) {
            continue;
          }
          for (odb::dbITerm* iterm : inst->getITerms()) {
            if (iterm == nullptr
                || iterm->getIoType() != odb::dbIoType::OUTPUT) {
              continue;
            }
            odb::dbNet* net = iterm->getNet();
            if (net == nullptr || net->getSigType() == odb::dbSigType::CLOCK) {
              continue;
            }
            for (odb::dbITerm* fanout_iterm : net->getITerms()) {
              if (fanout_iterm == nullptr
                  || fanout_iterm->getIoType() != odb::dbIoType::INPUT) {
                continue;
              }
              odb::dbInst* fanout_inst = fanout_iterm->getInst();
              if (fanout_inst == nullptr || cloud.count(fanout_inst) != 0) {
                continue;
              }
              if (fanout_inst->getMaster()->isBlock()
                  || !safeRestructureInst(fanout_inst)) {
                continue;
              }
              cloud.insert(fanout_inst);
              next_frontier.insert(fanout_inst);
              added++;
              if (added >= expand_side_fanout_max_add) {
                logger_->report(
                    "RMP_GUARD|expanded_side_fanout|levels={}|added={}|"
                    "instances={}|hit_limit=1",
                    expand_side_fanout_levels,
                    added,
                    cloud.size());
                return;
              }
            }
          }
        }
        if (next_frontier.empty()) {
          break;
        }
        frontier = std::move(next_frontier);
      }
      logger_->report(
          "RMP_GUARD|expanded_side_fanout|levels={}|added={}|instances={}|"
          "hit_limit=0",
          expand_side_fanout_levels,
          added,
          cloud.size());
    };
    auto prune_side_output_cloud
        = [&](std::set<odb::dbInst*>& cloud,
              const std::set<odb::dbInst*>& protected_insts,
              const std::string& target_output_net,
              const char* label) {
            if (!prune_side_outputs || prune_side_output_target <= 0
                || prune_side_output_max_remove <= 0 || cloud.empty()) {
              return;
            }

            const std::set<std::string> before_boundary_output_nets
                = cloudBoundaryOutputNetNames(cloud);
            const int before_boundary_outputs = before_boundary_output_nets.size();
            const bool target_required = !target_output_net.empty()
                                         && before_boundary_output_nets.count(
                                                target_output_net)
                                                != 0;
            int current_boundary_outputs = before_boundary_outputs;
            int removed = 0;
            int force_removed = 0;
            while (current_boundary_outputs > prune_side_output_target
                   && removed < prune_side_output_max_remove) {
              odb::dbInst* best_inst = nullptr;
              odb::dbInst* shrink_inst = nullptr;
              int best_boundary_outputs = current_boundary_outputs;
              int shrink_boundary_outputs = std::numeric_limits<int>::max();
              for (odb::dbInst* inst : cloud) {
                if (inst == nullptr) {
                  continue;
                }
                if (!prune_side_outputs_allow_path_removal
                    && protected_insts.count(inst) != 0) {
                  continue;
                }

                std::set<odb::dbInst*> trial_cloud = cloud;
                trial_cloud.erase(inst);
                if (trial_cloud.empty()) {
                  continue;
                }
                const int trial_boundary_outputs
                    = cloudBoundaryOutputCount(trial_cloud);
                if (target_required
                    && cloudBoundaryOutputNetNames(trial_cloud).count(
                           target_output_net)
                           == 0) {
                  continue;
                }
                if (trial_boundary_outputs < best_boundary_outputs) {
                  best_inst = inst;
                  best_boundary_outputs = trial_boundary_outputs;
                }
                if (prune_side_outputs_force_shrink
                    && static_cast<int>(cloud.size())
                           > prune_side_output_min_insts
                    && trial_boundary_outputs <= current_boundary_outputs
                    && trial_boundary_outputs < shrink_boundary_outputs) {
                  shrink_inst = inst;
                  shrink_boundary_outputs = trial_boundary_outputs;
                }
              }

              if (best_inst == nullptr) {
                if (!prune_side_outputs_force_shrink
                    || shrink_inst == nullptr) {
                  break;
                }
                best_inst = shrink_inst;
                best_boundary_outputs = shrink_boundary_outputs;
                force_removed++;
              }
              cloud.erase(best_inst);
              current_boundary_outputs = best_boundary_outputs;
              removed++;
            }

            logger_->report(
                "RMP_GUARD|pruned_side_outputs|label={}|before={}|after={}|"
                "target={}|removed={}|instances={}|protected={}|"
                "allow_path_removal={}|force_shrink={}|force_removed={}|"
                "min_insts={}|target_net={}|target_required={}",
                label,
                before_boundary_outputs,
                current_boundary_outputs,
                prune_side_output_target,
                removed,
                cloud.size(),
                protected_insts.size(),
                prune_side_outputs_allow_path_removal,
                prune_side_outputs_force_shrink,
                force_removed,
                prune_side_output_min_insts,
                target_output_net,
                target_required);
          };

    sta::StringSeq group_names;
    sta::PathEndSeq path_ends = sta_state->findPathEnds(nullptr,
                                                        nullptr,
                                                        nullptr,
                                                        false,
                                                        open_sta_->scenes(),
                                                        sta::MinMaxAll::max(),
                                                        group_path_count,
                                                        endpoint_path_count,
                                                        unique_path_pins,
                                                        unique_path_pins,
                                                        -sta::INF,
                                                        slack_threshold_,
                                                        true,
                                                        group_names,
                                                        true,
                                                        false,
                                                        false,
                                                        false,
                                                        false,
                                                        false);
    logger_->report("Number of paths for restructure are {}", path_ends.size());
    logger_->report(
        "RMP_GUARD|path_query|group_path_count={}|endpoint_path_count={}|"
        "unique_path_pins={}|union_endpoint_paths={}",
        group_path_count,
        endpoint_path_count,
        unique_path_pins,
        union_endpoint_paths);
    std::set<std::string> selected_endpoint_names;
    std::vector<std::string> endpoint_order;
    std::map<std::string, std::set<odb::dbInst*>> endpoint_clouds;
    std::map<std::string, std::set<odb::dbInst*>> endpoint_path_core_clouds;
    std::map<std::string, std::string> endpoint_target_net_names;
    std::map<std::string, int> endpoint_path_counts;
    std::map<std::string, sta::Slack> endpoint_slacks;
    int eligible_path_index = 0;
    int selected_clouds = 0;
    for (sta::PathEnd* path_end : path_ends) {
      sta::Path* path = path_end->path();
      if (path == nullptr) {
        continue;
      }

      sta::PathExpanded expanded(path, sta_state);
      // Members in expanded include gate output and net so divide by 2
      logger_->report("Found path of depth {}", expanded.size() / 2);
      if (expanded.size() / 2 > max_depth) {
        if (eligible_path_index++ < skip_paths) {
          continue;
        }
        if (skip_paths > 0) {
          logger_->report("RMP_GUARD|selected_path_after_skip|skip_paths={}",
                          skip_paths);
        }
        sta::Vertex* endpoint = path_end->vertex(sta_state);
        if (endpoint == nullptr || endpoint->pin() == nullptr) {
          continue;
        }
        const std::string endpoint_name
            = open_sta_->network()->pathName(endpoint->pin());
        std::string target_output_net_name;
        {
          odb::dbITerm* endpoint_term = nullptr;
          odb::dbBTerm* endpoint_port = nullptr;
          odb::dbModITerm* endpoint_moditerm = nullptr;
          open_sta_->getDbNetwork()->staToDb(
              endpoint->pin(), endpoint_term, endpoint_port, endpoint_moditerm);
          if (endpoint_term != nullptr && endpoint_term->getNet() != nullptr) {
            target_output_net_name = endpoint_term->getNet()->getName();
          } else if (endpoint_port != nullptr
                     && endpoint_port->getNet() != nullptr) {
            target_output_net_name = endpoint_port->getNet()->getName();
          }
        }
        if ((unique_endpoints || union_endpoint_paths)
            && selected_endpoint_names.find(endpoint_name)
                   != selected_endpoint_names.end()) {
          if (!union_endpoint_paths
              || endpoint_path_counts[endpoint_name] >= endpoint_path_count) {
            logger_->report("RMP_GUARD|skip_duplicate_endpoint|name={}",
                            endpoint_name);
            continue;
          }
        }
        if (union_endpoint_paths
            && selected_endpoint_names.find(endpoint_name)
                   == selected_endpoint_names.end()
            && selected_clouds >= max_clouds) {
          logger_->report("RMP_GUARD|skip_duplicate_endpoint|name={}",
                          endpoint_name);
          continue;
        }
        if (path_cone_halo_sampled_recipe) {
          const sta::Slack endpoint_slack = path_end->slack(sta_state);
          const auto [slack_itr, inserted]
              = endpoint_slacks.emplace(endpoint_name, endpoint_slack);
          if (!inserted) {
            slack_itr->second = std::min(slack_itr->second, endpoint_slack);
          }
        }
        if (selected_endpoint_names.insert(endpoint_name).second) {
          endpoint_order.push_back(endpoint_name);
          endpoint_target_net_names[endpoint_name] = target_output_net_name;
          logger_->report("RMP_GUARD|selected_endpoint|index={}|name={}",
                          selected_clouds,
                          endpoint_name);
          if (union_endpoint_paths) {
            selected_clouds++;
          }
        }
        if (endpoint->pin() != nullptr) {
          ends.insert(endpoint->pin());
        }
        std::vector<odb::dbInst*> path_candidates;
        std::set<odb::dbInst*> seen_path_insts;
        int raw_path_candidate_pins = 0;
        for (int index = expanded.startIndex(); index < expanded.size();
             ++index) {
          const sta::Path* expanded_path = expanded.path(index);
          if (expanded_path == nullptr) {
            continue;
          }
          const sta::Pin* pin = expanded_path->pin(sta_state);
          if (pin == nullptr) {
            continue;
          }
          odb::dbITerm* term = nullptr;
          odb::dbBTerm* port = nullptr;
          odb::dbModITerm* moditerm = nullptr;
          open_sta_->getDbNetwork()->staToDb(pin, term, port, moditerm);
          if (term && !term->getInst()->getMaster()->isBlock()
              && safeRestructureInst(term->getInst())) {
            raw_path_candidate_pins++;
            if (seen_path_insts.insert(term->getInst()).second) {
              path_candidates.push_back(term->getInst());
            }
          }
        }
        if (max_path_insts > 0
            && static_cast<int>(path_candidates.size()) > max_path_insts) {
          logger_->report(
              "RMP_GUARD|limit_path_cloud|candidate_pins={}|"
              "unique_candidates={}|max_path_insts={}",
              raw_path_candidate_pins,
              path_candidates.size(),
              max_path_insts);
          path_candidates.erase(path_candidates.begin(),
                                path_candidates.end() - max_path_insts);
        }
        if (union_endpoint_paths) {
          std::set<odb::dbInst*>& cloud = endpoint_clouds[endpoint_name];
          cloud.insert(path_candidates.begin(), path_candidates.end());
          std::set<odb::dbInst*>& path_core_cloud
              = endpoint_path_core_clouds[endpoint_name];
          path_core_cloud.insert(path_candidates.begin(), path_candidates.end());
          endpoint_path_counts[endpoint_name]++;
          logger_->report(
              "RMP_GUARD|selected_endpoint_path|name={}|path_count={}|"
              "cloud_instances={}|target_net={}",
              endpoint_name,
              endpoint_path_counts[endpoint_name],
              cloud.size(),
              target_output_net_name);
          continue;
        }
        if (!suffix_sweep_sizes.empty() && max_clouds > 1) {
          for (const int suffix_size : suffix_sweep_sizes) {
            if (selected_clouds >= max_clouds) {
              break;
            }
            if (path_candidates.empty()) {
              break;
            }
            const int suffix_begin
                = std::max(0,
                           static_cast<int>(path_candidates.size())
                               - suffix_size);
            std::set<odb::dbInst*> cloud(path_candidates.begin()
                                             + suffix_begin,
                                         path_candidates.end());
            if (cloud.empty()) {
              continue;
            }
            const std::set<odb::dbInst*> path_core = cloud;
            expand_side_fanin_cloud(cloud);
            expand_side_fanout_cloud(cloud);
            prune_side_output_cloud(
                cloud, path_core, target_output_net_name, "suffix");
            path_inst_cloud_names_.push_back(cloudInstNames(cloud));
            path_inst_cloud_target_net_names_.push_back(target_output_net_name);
            path_inst_clouds_.push_back(std::move(cloud));
            selected_clouds++;
            logger_->report(
                "RMP_GUARD|selected_sweep_cloud|index={}|instances={}|"
                "suffix_size={}|unique_candidates={}|boundary_outputs={}|"
                "target_net={}",
                selected_clouds - 1,
                path_inst_clouds_.back().size(),
                suffix_size,
                path_candidates.size(),
                cloudBoundaryOutputCount(path_inst_clouds_.back()),
                target_output_net_name);
          }
          path_insts_.clear();
          if (selected_clouds >= max_clouds) {
            break;
          }
          continue;
        }
        path_insts_.insert(path_candidates.begin(), path_candidates.end());
        path_inst_target_net_name_ = target_output_net_name;
        if (max_clouds > 1) {
          std::set<odb::dbInst*> cloud(path_candidates.begin(),
                                       path_candidates.end());
          if (!cloud.empty()) {
            const std::set<odb::dbInst*> path_core = cloud;
            expand_side_fanin_cloud(cloud);
            expand_side_fanout_cloud(cloud);
            prune_side_output_cloud(
                cloud, path_core, target_output_net_name, "path");
            path_inst_cloud_names_.push_back(cloudInstNames(cloud));
            path_inst_cloud_target_net_names_.push_back(target_output_net_name);
            path_inst_clouds_.push_back(std::move(cloud));
            selected_clouds++;
            logger_->report(
                "RMP_GUARD|selected_cloud|index={}|instances={}|"
                "boundary_outputs={}|target_net={}",
                            selected_clouds - 1,
                path_inst_clouds_.back().size(),
                cloudBoundaryOutputCount(path_inst_clouds_.back()),
                target_output_net_name);
          }
          path_insts_.clear();
          if (selected_clouds >= max_clouds) {
            break;
          }
          continue;
        }
        // Use only one end point to limit blob size for timing by default.
        break;
      }
    }
    if (union_endpoint_paths) {
      struct CloudRank
      {
        std::string endpoint_name;
        std::string target_net_name;
        sta::Slack endpoint_slack;
        LocalRmpCost local_cost;
        LocalWireCost wire_cost;
        std::set<std::string> inst_names;
        std::set<odb::dbInst*> cloud;
        int path_count;
        size_t construction_index;
      };
      std::vector<CloudRank> ranks;
      size_t construction_index = 0;
      for (const std::string& endpoint_name : endpoint_order) {
        auto cloud_itr = endpoint_clouds.find(endpoint_name);
        if (cloud_itr == endpoint_clouds.end() || cloud_itr->second.empty()) {
          continue;
        }
        const auto path_core_itr = endpoint_path_core_clouds.find(endpoint_name);
        const std::set<odb::dbInst*> empty_path_core;
        const std::set<odb::dbInst*>& path_core
            = path_core_itr != endpoint_path_core_clouds.end()
                  ? path_core_itr->second
                  : empty_path_core;
        expand_side_fanin_cloud(cloud_itr->second);
        expand_side_fanout_cloud(cloud_itr->second);
        const std::string target_output_net_name
            = endpoint_target_net_names[endpoint_name];
        prune_side_output_cloud(
            cloud_itr->second, path_core, target_output_net_name, "endpoint");
        if (path_cone_halo_sampled_recipe) {
          const bool legal_cloud
              = std::all_of(cloud_itr->second.begin(),
                            cloud_itr->second.end(),
                            [this](odb::dbInst* inst) {
                              return safeRestructureInst(inst);
                            });
          const bool preserves_path_core
              = std::includes(cloud_itr->second.begin(),
                              cloud_itr->second.end(),
                              path_core.begin(),
                              path_core.end());
          if (!legal_cloud || !preserves_path_core) {
            logger_->report(
                "RMP_GUARD|skip_path_cone_cloud|endpoint={}|instances={}|"
                "reason={}",
                endpoint_name,
                cloud_itr->second.size(),
                legal_cloud ? "path_core_removed" : "unsafe_live_inst");
            continue;
          }
        }
        CloudRank rank;
        rank.endpoint_name = endpoint_name;
        rank.target_net_name = target_output_net_name;
        rank.inst_names = cloudInstNames(cloud_itr->second);
        rank.cloud = std::move(cloud_itr->second);
        rank.path_count = endpoint_path_counts[endpoint_name];
        rank.construction_index = construction_index++;
        if (path_cone_halo_sampled_recipe) {
          rank.endpoint_slack = endpoint_slacks[endpoint_name];
          rank.local_cost = cloudLocalCost(open_sta_, rank.cloud);
          rank.wire_cost = cloudWireCost(rank.cloud);
        } else {
          rank.endpoint_slack = sta::INF;
        }
        ranks.push_back(std::move(rank));
      }
      if (path_cone_halo_sampled_recipe) {
        std::sort(ranks.begin(),
                  ranks.end(),
                  [](const CloudRank& lhs, const CloudRank& rhs) {
                    if (lhs.endpoint_slack != rhs.endpoint_slack) {
                      return lhs.endpoint_slack < rhs.endpoint_slack;
                    }
                    if (lhs.local_cost.leakage != rhs.local_cost.leakage) {
                      return lhs.local_cost.leakage < rhs.local_cost.leakage;
                    }
                    if (lhs.local_cost.area != rhs.local_cost.area) {
                      return lhs.local_cost.area < rhs.local_cost.area;
                    }
                    if (lhs.wire_cost.hpwl_dbu != rhs.wire_cost.hpwl_dbu) {
                      return lhs.wire_cost.hpwl_dbu < rhs.wire_cost.hpwl_dbu;
                    }
                    if (lhs.wire_cost.max_net_hpwl_dbu
                        != rhs.wire_cost.max_net_hpwl_dbu) {
                      return lhs.wire_cost.max_net_hpwl_dbu
                             < rhs.wire_cost.max_net_hpwl_dbu;
                    }
                    if (lhs.local_cost.inst_count != rhs.local_cost.inst_count) {
                      return lhs.local_cost.inst_count
                             < rhs.local_cost.inst_count;
                    }
                    if (lhs.inst_names != rhs.inst_names) {
                      return lhs.inst_names < rhs.inst_names;
                    }
                    return lhs.construction_index < rhs.construction_index;
                  });
      }
      for (size_t cloud_index = 0; cloud_index < ranks.size(); ++cloud_index) {
        CloudRank& rank = ranks[cloud_index];
        if (path_cone_halo_sampled_recipe) {
          logger_->report(
              "RMP_GUARD|path_cone_sampled_rank|rank={}|endpoint={}|"
              "endpoint_slack={}|leakage={}|area={}|hpwl={}|instances={}",
              cloud_index,
              rank.endpoint_name,
              rank.endpoint_slack,
              rank.local_cost.leakage,
              rank.local_cost.area,
              rank.wire_cost.hpwl_dbu,
              rank.local_cost.inst_count);
        }
        path_inst_cloud_names_.push_back(rank.inst_names);
        path_inst_cloud_target_net_names_.push_back(rank.target_net_name);
        path_inst_clouds_.push_back(std::move(rank.cloud));
        logger_->report(
            "RMP_GUARD|selected_cloud|index={}|instances={}|endpoint={}|"
            "paths={}|boundary_outputs={}|target_net={}",
            cloud_index,
            path_inst_clouds_.back().size(),
            rank.endpoint_name,
            rank.path_count,
            cloudBoundaryOutputCount(path_inst_clouds_.back()),
            rank.target_net_name);
      }
      if (path_cone_halo_sampled_recipe && !ranks.empty()) {
        logger_->report("METRIC|rmp_path_cone_admission_decided|{}",
                        ranks.size());
      }
      path_insts_.clear();
    }
  } else {
    sta::VertexSet& end_points = sta_state->endpoints();
    logger_->report("Number of paths for restructure are {}",
                    end_points.size());
    for (auto& end_point : end_points) {
      ends.insert(end_point->pin());
    }
  }

  // unconstrained end points
  if (is_area_mode_) {
    auto errors = open_sta_->checkTiming(open_sta_->cmdMode(),
                                         false /*no_input_delay*/,
                                         false /*no_output_delay*/,
                                         false /*reg_multiple_clks*/,
                                         true /*reg_no_clks*/,
                                         true /*unconstrained_endpoints*/,
                                         false /*loops*/,
                                         false /*generated_clks*/);
    debugPrint(logger_, RMP, "remap", 1, "Size of errors = {}", errors.size());
    if (!errors.empty() && errors[0]->size() > 1) {
      sta::CheckError* error = errors[0];
      bool first = true;
      for (auto pin_name : *error) {
        debugPrint(logger_, RMP, "remap", 1, "Unconstrained pin: {}", pin_name);
        if (!first && open_sta_->getDbNetwork()->findPin(pin_name.c_str())) {
          ends.insert(open_sta_->getDbNetwork()->findPin(pin_name.c_str()));
        }
        first = false;
      }
    }
    if (errors.size() > 1 && errors[1]->size() > 1) {
      sta::CheckError* error = errors[1];
      bool first = true;
      for (auto pin_name : *error) {
        debugPrint(logger_, RMP, "remap", 1, "Unclocked pin: {}", pin_name);
        if (!first && open_sta_->getDbNetwork()->findPin(pin_name.c_str())) {
          ends.insert(open_sta_->getDbNetwork()->findPin(pin_name.c_str()));
        }
        first = false;
      }
    }
  }
  logger_->report("Found {} end points for restructure", ends.size());
}

int Restructure::countConsts(odb::dbBlock* top_block)
{
  int const_nets = 0;
  for (auto block_net : top_block->getNets()) {
    if (block_net->getSigType().isSupply()) {
      const_nets++;
    }
  }

  return const_nets;
}

void Restructure::removeConstCells()
{
  if (hicell_.empty() || locell_.empty()) {
    return;
  }

  odb::dbMaster* hicell_master = nullptr;
  odb::dbMTerm* hiterm = nullptr;
  odb::dbMaster* locell_master = nullptr;
  odb::dbMTerm* loterm = nullptr;

  for (auto&& lib : block_->getDb()->getLibs()) {
    hicell_master = lib->findMaster(hicell_.c_str());

    locell_master = lib->findMaster(locell_.c_str());
    if (locell_master && hicell_master) {
      break;
    }
  }
  if (!hicell_master || !locell_master) {
    return;
  }

  hiterm = hicell_master->findMTerm(hiport_.c_str());
  loterm = locell_master->findMTerm(loport_.c_str());
  if (!hiterm || !loterm) {
    return;
  }

  open_sta_->clearLogicConstants();
  open_sta_->findLogicConstants();
  std::set<odb::dbInst*> const_insts;
  int const_cnt = 1;
  for (auto inst : block_->getInsts()) {
    int outputs = 0;
    int const_outputs = 0;
    auto master = inst->getMaster();
    sta::LibertyCell* cell = open_sta_->getDbNetwork()->libertyCell(
        open_sta_->getDbNetwork()->dbToSta(master));
    if (cell == nullptr) {
      continue;
    }
    if (cell->hasSequentials()) {
      continue;
    }

    for (auto&& iterm : inst->getITerms()) {
      if (iterm->getSigType() == odb::dbSigType::POWER
          || iterm->getSigType() == odb::dbSigType::GROUND) {
        continue;
      }

      if (iterm->getIoType() != odb::dbIoType::OUTPUT) {
        continue;
      }
      outputs++;
      auto pin = open_sta_->getDbNetwork()->dbToSta(iterm);
      sta::LogicValue pin_val
          = open_sta_->simLogicValue(pin, open_sta_->cmdMode());
      if (pin_val == sta::LogicValue::one || pin_val == sta::LogicValue::zero) {
        odb::dbNet* net = iterm->getNet();
        if (net) {
          odb::dbMaster* const_master = (pin_val == sta::LogicValue::one)
                                            ? hicell_master
                                            : locell_master;
          odb::dbMTerm* const_port
              = (pin_val == sta::LogicValue::one) ? hiterm : loterm;
          std::string inst_name = "rmp_const_" + std::to_string(const_cnt);
          debugPrint(logger_,
                     RMP,
                     "remap",
                     2,
                     "Adding cell {} inst {} for {}",
                     const_master->getName(),
                     inst_name,
                     inst->getName());
          auto new_inst
              = odb::dbInst::create(block_, const_master, inst_name.c_str());
          if (new_inst) {
            iterm->disconnect();
            new_inst->getITerm(const_port)->connect(net);
          } else {
            logger_->warn(RMP, 9, "Could not create instance {}.", inst_name);
          }
        }
        const_outputs++;
        const_cnt++;
      }
    }
    if (outputs > 0 && outputs == const_outputs) {
      const_insts.insert(inst);
    }
  }
  open_sta_->clearLogicConstants();

  debugPrint(
      logger_, RMP, "remap", 2, "Removing {} instances...", const_insts.size());

  for (auto inst : const_insts) {
    removeConstCell(inst);
  }
  logger_->report("Removed {} instances with constant outputs.",
                  const_insts.size());
}

void Restructure::removeConstCell(odb::dbInst* inst)
{
  for (auto iterm : inst->getITerms()) {
    iterm->disconnect();
  }
  odb::dbInst::destroy(inst);
}

bool Restructure::writeAbcScript(const std::string& file_name)
{
  std::ofstream script(file_name.c_str());

  if (!script.is_open()) {
    logger_->error(RMP, 3, "Cannot open file {} for writing.", file_name);
    return false;
  }

  for (const auto& lib_name : lib_file_names_) {
    std::string extension = std::filesystem::path(lib_name).extension().string();
    std::transform(extension.begin(), extension.end(), extension.begin(),
                   [](unsigned char ch) { return std::tolower(ch); });
    if (extension == ".genlib") {
      script << "read_library " << lib_name << '\n';
    } else {
      // abc read_lib prints verbose by default, -v toggles to off to avoid read
      // time being printed
      script << "read_lib -v " << lib_name << '\n';
    }
  }

  script << "read_blif -n " << input_blif_file_name_ << '\n';

  if (logger_->debugCheck(RMP, "remap", 1)) {
    script << "write_verilog " << input_blif_file_name_ + std::string(".v")
           << '\n';
  }

  writeOptCommands(script);

  script << "write_blif " << output_blif_file_name_ << '\n';

  if (logger_->debugCheck(RMP, "remap", 1)) {
    script << "write_verilog " << output_blif_file_name_ + std::string(".v")
           << '\n';
  }

  script.close();

  return true;
}

void Restructure::writeOptCommands(std::ofstream& script)
{
  const char* variant_env = std::getenv("RMP_SCRIPT_VARIANT");
  const std::string variant = variant_env == nullptr ? "" : variant_env;

  if (variant == "map_only") {
    script << "bdd; sop\n";
    script << "map -p\n";
    script << "buffer -p -c\n";
    return;
  }

  if (variant == "genlib_safe") {
    script << "bdd; sop\n";
    script << "alias resyn2 \"balance; rewrite; refactor; balance; rewrite; "
              "rewrite -z; balance; refactor -z; rewrite -z; balance\""
           << '\n';
    script << "resyn2\n";
    script << "map -p\n";
    return;
  }

  std::string choice
      = "alias choice \"fraig_store; resyn2; fraig_store; resyn2; fraig_store; "
        "fraig_restore\"";
  std::string choice2
      = "alias choice2 \"fraig_store; balance; fraig_store; resyn2; "
        "fraig_store; resyn2; fraig_store; resyn2; fraig_store; "
        "fraig_restore\"";
  script << "bdd; sop\n";

  script << "alias resyn2 \"balance; rewrite; refactor; balance; rewrite; "
            "rewrite -z; balance; refactor -z; rewrite -z; balance\""
         << '\n';
  script << choice << '\n';
  script << choice2 << '\n';

  if (opt_mode_ == Mode::kArea3) {
    script << "choice2\n";  // << "scleanup" << std::endl;
  } else {
    script << "resyn2\n";  // << "scleanup" << std::endl;
  }

  if (variant == "relaxed_delay") {
    script << "map -D 1000 -A 0.9 -B 0.2 -M 0 -p\n";
    script << "buffer -p -c\n";
    return;
  }

  switch (opt_mode_) {
    case Mode::kDelay1: {
      script << "map -D 0.01 -A 0.9 -B 0.2 -M 0 -p\n";
      script << "buffer -p -c\n";
      break;
    }
    case Mode::kDelay2: {
      script << "choice\n";
      script << "map -D 0.01 -A 0.9 -B 0.2 -M 0 -p\n";
      script << "choice\n";
      script << "map -D 0.01\n";
      script << "buffer -p -c\n"
             << "topo\n";
      break;
    }
    case Mode::kDelay3: {
      script << "choice2\n";
      script << "map -D 0.01 -A 0.9 -B 0.2 -M 0 -p\n";
      script << "choice2\n";
      script << "map -D 0.01\n";
      script << "buffer -p -c\n"
             << "topo\n";
      break;
    }
    case Mode::kDelay4: {
      script << "choice2\n";
      script << "amap -F 20 -A 20 -C 5000 -Q 0.1 -m\n";
      script << "choice2\n";
      script << "map -D 0.01 -A 0.9 -B 0.2 -M 0 -p\n";
      script << "buffer -p -c\n";
      break;
    }
    case Mode::kArea2:
    case Mode::kArea3: {
      script << "choice2\n";
      script << "amap -m -Q 0.1 -F 20 -A 20 -C 5000\n";
      script << "choice2\n";
      script << "amap -m -Q 0.1 -F 20 -A 20 -C 5000\n";
      break;
    }
    case Mode::kArea1:
    default: {
      script << "choice2\n";
      script << "amap -m -Q 0.1 -F 20 -A 20 -C 5000\n";
      break;
    }
  }
}

void Restructure::setMode(const char* mode_name)
{
  is_area_mode_ = true;

  if (!strcmp(mode_name, "timing") || !strcmp(mode_name, "delay")) {
    is_area_mode_ = false;
    opt_mode_ = Mode::kDelay1;
  } else if (!strcmp(mode_name, "area")) {
    opt_mode_ = Mode::kArea1;
  } else {
    logger_->warn(RMP, 10, "Mode {} not recognized.", mode_name);
  }
}

void Restructure::setTieHiPort(sta::LibertyPort* tie_hi_port)
{
  if (tie_hi_port) {
    hicell_ = tie_hi_port->libertyCell()->name();
    hiport_ = tie_hi_port->name();
  }
}

void Restructure::setTieLoPort(sta::LibertyPort* tie_lo_port)
{
  if (tie_lo_port) {
    locell_ = tie_lo_port->libertyCell()->name();
    loport_ = tie_lo_port->name();
  }
}

bool Restructure::readAbcLog(const std::string& abc_file_name,
                             int& level_gain,
                             float& final_delay)
{
  std::ifstream abc_file(abc_file_name);
  if (abc_file.bad()) {
    logger_->error(RMP, 2, "cannot open file {}", abc_file_name);
    return false;
  }
  debugPrint(
      logger_, utl::RMP, "remap", 1, "Reading ABC log {}.", abc_file_name);
  std::string buf;
  const char delimiter = ' ';
  bool status = true;
  std::vector<double> level;
  std::vector<float> delay;

  // read the file line by line
  while (std::getline(abc_file, buf)) {
    // convert the line in to stream:
    std::istringstream ss(buf);
    std::vector<std::string> tokens;

    // read the line, word by word
    while (std::getline(ss, buf, delimiter)) {
      tokens.push_back(buf);
    }

    if (!tokens.empty() && tokens[0] == "Error:") {
      status = false;
      logger_->warn(RMP,
                    5,
                    "ABC run failed, see log file {} for details.",
                    abc_file_name);
      break;
    }
    if (tokens.size() > 7 && tokens[tokens.size() - 3] == "lev"
        && tokens[tokens.size() - 2] == "=") {
      level.emplace_back(std::stoi(tokens[tokens.size() - 1]));
    }
    if (tokens.size() > 7) {
      std::string prev_token;
      for (std::string token : tokens) {
        if (prev_token == "delay" && token.at(0) == '=') {
          std::string delay_str = token;
          if (delay_str.size() > 1) {
            delay_str.erase(
                delay_str.begin());  // remove first char which is '='
            delay.emplace_back(std::stof(delay_str));
          }
          break;
        }
        prev_token = std::move(token);
      }
    }
  }

  if (level.size() > 1) {
    level_gain = level[0] - level[level.size() - 1];
  }
  if (!delay.empty()) {
    final_delay = delay[delay.size() - 1];  // last value in file
  }
  return status;
}
}  // namespace rmp
