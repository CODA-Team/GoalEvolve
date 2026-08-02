// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "CloneGenerator.hh"

#include <algorithm>
#include <cstdlib>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "CloneCandidate.hh"
#include "MoveCandidate.hh"
#include "MoveCommitter.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "db_sta/dbNetwork.hh"
#include "db_sta/dbSta.hh"
#include "odb/geom.h"
#include "rsz/Resizer.hh"
#include "sta/Delay.hh"
#include "sta/Graph.hh"
#include "sta/GraphClass.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/Path.hh"
#include "sta/Transition.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

namespace {

constexpr int kCloneMinFanout = 5;
constexpr int kCloneCriticalMinFanout = 3;
constexpr float kCloneCriticalSlack = -1.8e-10f;

using FanoutSlack = std::pair<sta::Vertex*, sta::Slack>;

int envInt(const char* name, const int default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  char* end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  return end != value && *end == '\0' ? static_cast<int>(parsed)
                                      : default_value;
}

bool envFlag(const char* name, const bool default_value = false)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) == "1" || std::string(value) == "true"
         || std::string(value) == "TRUE" || std::string(value) == "yes";
}

bool hasUsefulCloneFanout(const Target& target)
{
  const int min_fanout = target.slack <= kCloneCriticalSlack
                             ? kCloneCriticalMinFanout
                             : kCloneMinFanout;
  return target.fanout >= min_fanout;
}

bool resolveDriverTarget(Resizer& resizer,
                         const Target& target,
                         sta::Pin*& drvr_pin,
                         sta::Instance*& drvr_inst,
                         sta::Instance*& parent,
                         sta::LibertyCell*& original_cell,
                         sta::Vertex*& drvr_vertex)
{
  // Keep cloning limited to high-fanout combinational drivers that can absorb
  // the split.
  if (!target.canBePathDriver()) {
    return false;
  }

  drvr_vertex = target.vertex(resizer);
  if (drvr_vertex == nullptr) {
    return false;
  }

  drvr_pin = target.resolvedPin(resizer);
  if (drvr_pin == nullptr) {
    return false;
  }
  if (!hasUsefulCloneFanout(target)) {
    debugPrint(resizer.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Fanout {} too low for slack {}",
               resizer.network()->pathName(drvr_pin),
               target.fanout,
               target.slack);
    return false;
  }
  if (resizer.sta()->isClock(drvr_pin, resizer.sta()->cmdMode())) {
    debugPrint(resizer.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Driver pin is on a clock path",
               resizer.network()->pathName(drvr_pin));
    return false;
  }
  if (!resizer.okToBufferNet(drvr_pin)) {
    debugPrint(resizer.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Not OK to buffer net",
               resizer.network()->pathName(drvr_pin));
    return false;
  }

  drvr_inst = resizer.dbNetwork()->instance(drvr_pin);
  if (drvr_inst == nullptr) {
    return false;
  }
  sta::Net* net = resizer.network()->net(drvr_pin);
  if (net != nullptr && resizer.sta()->isClock(net, resizer.sta()->cmdMode())) {
    debugPrint(resizer.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Driver net is a clock net",
               resizer.network()->pathName(drvr_pin));
    return false;
  }
  if (resizer.drivesSequentialClockPin(drvr_inst)) {
    debugPrint(resizer.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Driver feeds a sequential clock pin",
               resizer.network()->pathName(drvr_pin));
    return false;
  }
  if (!resizer.isSingleOutputCombinational(drvr_inst)) {
    debugPrint(resizer.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Not single output combinational",
               resizer.network()->pathName(drvr_pin));
    return false;
  }

  parent = resizer.dbNetwork()->getOwningInstanceParent(drvr_pin);
  original_cell = resizer.network()->libertyCell(drvr_inst);
  return parent != nullptr && original_cell != nullptr;
}

std::vector<FanoutSlack> collectFanoutSlacks(Resizer& resizer,
                                             const Target& target,
                                             sta::Vertex* drvr_vertex)
{
  std::vector<FanoutSlack> fanout_slacks;
  // Rank fanouts by slack delta so the clone steals the easiest half of the
  // load set.
  const sta::Path* driver_path = target.driverPath(resizer);
  const sta::RiseFall* rf = driver_path != nullptr
                                ? driver_path->transition(resizer.staState())
                                : nullptr;
  if (rf == nullptr) {
    return fanout_slacks;
  }

  const sta::Slack drvr_slack
      = resizer.sta()->slack(drvr_vertex, rf, resizer.maxAnalysisMode());
  sta::VertexOutEdgeIterator edge_iter(drvr_vertex, resizer.graph());
  while (edge_iter.hasNext()) {
    sta::Edge* edge = edge_iter.next();
    sta::Vertex* fanout_vertex = edge->to(resizer.graph());
    const sta::Slack fanout_slack
        = resizer.sta()->slack(fanout_vertex, rf, resizer.maxAnalysisMode());
    fanout_slacks.emplace_back(fanout_vertex, fanout_slack - drvr_slack);
  }

  std::ranges::sort(fanout_slacks,
                    [&resizer](const FanoutSlack& lhs, const FanoutSlack& rhs) {
                      return lhs.second > rhs.second
                             || (lhs.second == rhs.second
                                 && resizer.network()->pathNameLess(
                                     lhs.first->pin(), rhs.first->pin()));
                    });
  return fanout_slacks;
}

sta::LibertyCell* chooseCloneCell(Resizer& resizer,
                                  sta::LibertyCell* original_cell)
{
  sta::LibertyCell* clone_cell = resizer.halfDrivingPowerCell(original_cell);
  return clone_cell != nullptr ? clone_cell : original_cell;
}

std::vector<sta::LibertyCell*> chooseCloneCells(Resizer& resizer,
                                                const Target& target,
                                                sta::LibertyCell* original_cell)
{
  std::vector<sta::LibertyCell*> cells;
  auto add_cell = [&cells](sta::LibertyCell* cell) {
    if (cell == nullptr
        || std::find(cells.begin(), cells.end(), cell) != cells.end()) {
      return;
    }
    cells.push_back(cell);
  };

  add_cell(chooseCloneCell(resizer, original_cell));
  add_cell(original_cell);

  const bool enumerate_stronger
      = envFlag("RSZ_CLONE_ENUMERATE_STRONGER_CELLS", true)
        && target.slack <= -1.2e-10f;
  if (!enumerate_stronger) {
    return cells;
  }

  const int max_cells = std::clamp(envInt("RSZ_CLONE_MAX_CELLS", 3), 1, 6);
  sta::LibertyCellSeq swappable = resizer.getSwappableCells(original_cell);
  std::ranges::sort(swappable,
                    [&resizer](const sta::LibertyCell* lhs,
                               const sta::LibertyCell* rhs) {
                      return resizer.cellDriveResistance(lhs)
                             < resizer.cellDriveResistance(rhs);
                    });
  const float original_drive = resizer.cellDriveResistance(original_cell);
  for (sta::LibertyCell* cell : swappable) {
    if (cell == nullptr) {
      continue;
    }
    const bool at_least_as_strong
        = resizer.cellDriveResistance(cell) <= original_drive;
    if (!at_least_as_strong) {
      continue;
    }
    add_cell(cell);
    if (static_cast<int>(cells.size()) >= max_cells) {
      break;
    }
  }
  return cells;
}

std::vector<sta::Pin*> selectMovedLoads(
    Resizer& resizer,
    const std::vector<FanoutSlack>& fanout_slacks,
    const int requested_count,
    const bool move_most_critical)
{
  std::vector<sta::Pin*> moved_loads;
  const int load_count = static_cast<int>(fanout_slacks.size());
  const int split_count
      = std::clamp(requested_count, 1, std::max(1, load_count - 1));
  moved_loads.reserve(split_count);
  const int begin = move_most_critical ? load_count - split_count : 0;
  const int end = move_most_critical ? load_count : split_count;
  for (int i = begin; i < end; ++i) {
    sta::Pin* load_pin = fanout_slacks[i].first->pin();
    if (!resizer.network()->isTopLevelPort(load_pin)) {
      moved_loads.push_back(load_pin);
    }
  }
  return moved_loads;
}

odb::Point computeCloneLocation(Resizer& resizer,
                                sta::Pin* drvr_pin,
                                const std::vector<sta::Pin*>& moved_loads)
{
  int count = 1;
  int centroid_x = resizer.dbNetwork()->location(drvr_pin).getX();
  int centroid_y = resizer.dbNetwork()->location(drvr_pin).getY();
  for (sta::Pin* load_pin : moved_loads) {
    centroid_x += resizer.dbNetwork()->location(load_pin).getX();
    centroid_y += resizer.dbNetwork()->location(load_pin).getY();
    ++count;
  }
  return {centroid_x / count, centroid_y / count};
}

std::vector<int> cloneSplitCounts(const int fanout)
{
  std::vector<int> counts;
  const int max_counts
      = std::clamp(envInt("RSZ_CLONE_MAX_SPLIT_COUNTS", 3), 1, 5);
  const int quarter = std::max(1, fanout / 4);
  const int half = std::max(1, fanout / 2);
  const int three_quarter = std::max(1, (3 * fanout) / 4);
  for (const int count : {1, quarter, half, three_quarter, fanout - 1}) {
    const int clamped = std::clamp(count, 1, std::max(1, fanout - 1));
    if (std::find(counts.begin(), counts.end(), clamped) == counts.end()) {
      counts.push_back(clamped);
    }
    if (static_cast<int>(counts.size()) >= max_counts) {
      break;
    }
  }
  return counts;
}

std::string loadSignature(Resizer& resizer,
                          const std::vector<sta::Pin*>& moved_loads,
                          sta::LibertyCell* clone_cell)
{
  std::vector<std::string> names;
  names.reserve(moved_loads.size());
  for (sta::Pin* load_pin : moved_loads) {
    names.push_back(resizer.network()->pathName(load_pin));
  }
  std::ranges::sort(names);
  std::string signature = clone_cell != nullptr ? clone_cell->name() : "";
  for (const std::string& name : names) {
    signature += "|";
    signature += name;
  }
  return signature;
}

}  // namespace

CloneGenerator::CloneGenerator(const GeneratorContext& context)
    : MoveGenerator(context)
{
}

std::vector<std::unique_ptr<MoveCandidate>> CloneGenerator::generate(
    const Target& target)
{
  std::vector<std::unique_ptr<MoveCandidate>> candidates;
  sta::Pin* drvr_pin = nullptr;
  sta::Instance* drvr_inst = nullptr;
  sta::Instance* parent = nullptr;
  sta::LibertyCell* original_cell = nullptr;
  sta::Vertex* drvr_vertex = nullptr;
  if (!resolveDriverTarget(resizer_,
                           target,
                           drvr_pin,
                           drvr_inst,
                           parent,
                           original_cell,
                           drvr_vertex)) {
    return candidates;
  }

  if (committer_.hasPendingMoves(MoveType::kBuffer, drvr_inst)) {
    debugPrint(resizer_.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Has pending BufferMove",
               resizer_.network()->pathName(drvr_pin));
    return candidates;
  }
  if (committer_.hasPendingMoves(MoveType::kSplitLoad, drvr_inst)) {
    debugPrint(resizer_.logger(),
               RSZ,
               "clone_move",
               2,
               "REJECT CloneMove {}: Has pending SplitLoadMove",
               resizer_.network()->pathName(drvr_pin));
    return candidates;
  }

  std::vector<FanoutSlack> fanout_slacks
      = collectFanoutSlacks(resizer_, target, drvr_vertex);
  if (fanout_slacks.empty()) {
    return candidates;
  }

  const bool include_critical_branch
      = envFlag("RSZ_CLONE_CRITICAL_BRANCH_VARIANTS", true)
        && target.slack <= -9.0e-11f;
  std::vector<bool> modes = {false};
  if (include_critical_branch) {
    modes.push_back(true);
  }

  std::set<std::string> seen;
  const std::vector<int> counts = cloneSplitCounts(fanout_slacks.size());
  const std::vector<sta::LibertyCell*> clone_cells
      = chooseCloneCells(resizer_, target, original_cell);
  for (const bool move_most_critical : modes) {
    for (const int count : counts) {
      for (sta::LibertyCell* clone_cell : clone_cells) {
        std::vector<sta::Pin*> moved_loads = selectMovedLoads(
            resizer_, fanout_slacks, count, move_most_critical);
        if (moved_loads.empty()) {
          continue;
        }
        const std::string signature
            = loadSignature(resizer_, moved_loads, clone_cell);
        if (!seen.insert(signature).second) {
          continue;
        }
        const odb::Point clone_loc
            = computeCloneLocation(resizer_, drvr_pin, moved_loads);
        candidates.push_back(
            std::make_unique<CloneCandidate>(resizer_,
                                             target,
                                             drvr_pin,
                                             drvr_inst,
                                             parent,
                                             original_cell,
                                             clone_cell,
                                             clone_loc,
                                             std::move(moved_loads)));
      }
    }
  }
  return candidates;
}

}  // namespace rsz
