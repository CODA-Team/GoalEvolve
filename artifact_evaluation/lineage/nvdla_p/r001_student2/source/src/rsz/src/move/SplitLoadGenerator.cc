// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SplitLoadGenerator.hh"

#include <algorithm>
#include <cstdlib>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "SplitLoadCandidate.hh"
#include "db_sta/dbNetwork.hh"
#include "db_sta/dbSta.hh"
#include "odb/db.h"
#include "odb/geom.h"
#include "rsz/Resizer.hh"
#include "sta/Delay.hh"
#include "sta/Graph.hh"
#include "sta/GraphClass.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/Path.hh"
#include "sta/Transition.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

namespace {

constexpr int kSplitLoadMinFanout = 5;
constexpr int kSplitLoadCriticalMinFanout = 3;
constexpr float kSplitLoadCriticalSlack = -1.8e-10f;
using FanoutSlack = std::pair<sta::Vertex*, sta::Slack>;

bool envFlag(const char* name, const bool default_value = false)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) == "1" || std::string(value) == "true"
         || std::string(value) == "TRUE" || std::string(value) == "yes";
}

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

float envFloat(const char* name, const float default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  char* end = nullptr;
  const float parsed = std::strtof(value, &end);
  return end != value && *end == '\0' ? parsed : default_value;
}

bool hasUsefulSplitFanout(const Target& target)
{
  if (envFlag("RSZ_LOW_POWER_TIMING_BUFFER_GUARD", false)) {
    const int min_fanout
        = envInt("RSZ_SPLIT_LOW_POWER_MIN_FANOUT", kSplitLoadMinFanout + 2);
    const float critical_slack
        = envFloat("RSZ_SPLIT_LOW_POWER_CRITICAL_SLACK", -3.0e-10f);
    if (target.fanout < min_fanout && target.slack > critical_slack) {
      return false;
    }
  }
  const bool allow_low_fanout
      = envFlag("RSZ_SPLIT_LOAD_ALLOW_LOW_FANOUT", false);
  const float critical_slack
      = allow_low_fanout ? -9.0e-11f : kSplitLoadCriticalSlack;
  const int critical_min_fanout
      = allow_low_fanout ? 2 : kSplitLoadCriticalMinFanout;
  const int min_fanout
      = target.slack <= critical_slack ? critical_min_fanout
                                       : kSplitLoadMinFanout;
  return target.fanout >= min_fanout;
}

bool resolveDriverPin(Resizer& resizer,
                      const Target& target,
                      sta::Pin*& drvr_pin,
                      sta::Vertex*& drvr_vertex)
{
  if (!target.canBePathDriver()) {
    return false;
  }

  drvr_pin = target.resolvedPin(resizer);
  if (drvr_pin == nullptr) {
    return false;
  }
  if (!hasUsefulSplitFanout(target)) {
    debugPrint(resizer.logger(),
               RSZ,
               "split_load_move",
               2,
               "REJECT SplitLoadMove {}: Fanout {} too low for slack {}",
               resizer.network()->pathName(drvr_pin),
               target.fanout,
               target.slack);
    return false;
  }
  if (resizer.sta()->isClock(drvr_pin, resizer.sta()->cmdMode())) {
    debugPrint(resizer.logger(),
               RSZ,
               "split_load_move",
               2,
               "REJECT SplitLoadMove {}: Driver pin is on a clock path",
               resizer.network()->pathName(drvr_pin));
    return false;
  }
  if (!resizer.okToBufferNet(drvr_pin)) {
    debugPrint(resizer.logger(),
               RSZ,
               "split_load_move",
               2,
               "REJECT SplitLoadMove {}: Not OK to buffer net",
               resizer.network()->pathName(drvr_pin));
    return false;
  }

  drvr_vertex = target.vertex(resizer);
  sta::Net* net = resizer.network()->net(drvr_pin);
  if (net != nullptr && resizer.sta()->isClock(net, resizer.sta()->cmdMode())) {
    debugPrint(resizer.logger(),
               RSZ,
               "split_load_move",
               2,
               "REJECT SplitLoadMove {}: Driver net is a clock net",
               resizer.network()->pathName(drvr_pin));
    return false;
  }
  return drvr_vertex != nullptr;
}

std::vector<FanoutSlack> collectRankedFanoutSlacks(Resizer& resizer,
                                                   const Target& target,
                                                   sta::Vertex* drvr_vertex)
{
  std::vector<FanoutSlack> fanout_slacks;
  const sta::Path* driver_path = target.driverPath(resizer);
  const sta::RiseFall* rf = driver_path != nullptr
                                ? driver_path->transition(resizer.staState())
                                : nullptr;
  if (rf == nullptr) {
    return fanout_slacks;
  }

  const sta::Slack drvr_slack
      = resizer.sta()->slack(drvr_vertex, resizer.maxAnalysisMode());
  sta::VertexOutEdgeIterator edge_iter(drvr_vertex, resizer.graph());
  while (edge_iter.hasNext()) {
    sta::Edge* edge = edge_iter.next();
    if (!edge->isWire()) {
      continue;
    }

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

std::unique_ptr<sta::PinSet> chooseSplitLoads(
    Resizer& resizer,
    const std::vector<FanoutSlack>& fanout_slacks,
    const int requested_count,
    const bool move_most_critical)
{
  auto load_pins = std::make_unique<sta::PinSet>(resizer.network());
  const int load_count = static_cast<int>(fanout_slacks.size());
  const int split_count
      = std::clamp(requested_count, 1, std::max(1, load_count - 1));
  const int begin = move_most_critical ? load_count - split_count : 0;
  const int end = move_most_critical ? load_count : split_count;
  for (int i = begin; i < end; ++i) {
    sta::Pin* split_pin = fanout_slacks[i].first->pin();
    if (!resizer.network()->isTopLevelPort(split_pin)) {
      load_pins->insert(split_pin);
    }
  }
  return load_pins;
}

odb::Point computeSplitLocation(Resizer& resizer,
                                sta::Pin* drvr_pin,
                                const std::vector<FanoutSlack>& fanout_slacks,
                                const int requested_count,
                                const bool move_most_critical)
{
  int count = 1;
  int centroid_x = resizer.dbNetwork()->location(drvr_pin).getX();
  int centroid_y = resizer.dbNetwork()->location(drvr_pin).getY();
  const int load_count = static_cast<int>(fanout_slacks.size());
  const int split_count
      = std::clamp(requested_count, 1, std::max(1, load_count - 1));
  const int begin = move_most_critical ? load_count - split_count : 0;
  const int end = move_most_critical ? load_count : split_count;
  for (int i = begin; i < end; ++i) {
    sta::Pin* load_pin = fanout_slacks[i].first->pin();
    if (resizer.network()->isTopLevelPort(load_pin)) {
      continue;
    }
    centroid_x += resizer.dbNetwork()->location(load_pin).getX();
    centroid_y += resizer.dbNetwork()->location(load_pin).getY();
    ++count;
  }
  return {centroid_x / count, centroid_y / count};
}

std::vector<int> splitCounts(const int fanout)
{
  std::vector<int> counts;
  const int max_splits
      = std::clamp(envInt("RSZ_SPLIT_LOAD_MAX_SPLIT_COUNTS", 3), 1, 4);
  const int quarter = std::max(1, fanout / 4);
  const int half = std::max(1, fanout / 2);
  const int three_quarter = std::max(1, (3 * fanout) / 4);
  for (const int count : {1, quarter, half, three_quarter, fanout - 1}) {
    const int clamped = std::clamp(count, 1, std::max(1, fanout - 1));
    if (std::find(counts.begin(), counts.end(), clamped) == counts.end()) {
      counts.push_back(clamped);
    }
    if (static_cast<int>(counts.size()) >= max_splits) {
      break;
    }
  }
  return counts;
}

std::vector<sta::LibertyCell*> splitBufferCells(Resizer& resizer)
{
  std::vector<sta::LibertyCell*> cells;
  sta::LibertyCell* lowest = resizer.lowestDriveBufferCell();
  if (lowest == nullptr) {
    return cells;
  }
  cells.push_back(lowest);

  const int max_cells = std::clamp(envInt("RSZ_SPLIT_LOAD_MAX_BUFFER_CELLS", 3), 1, 6);
  sta::LibertyCellSeq swappable = resizer.getSwappableCells(lowest);
  std::ranges::sort(swappable,
                    [&resizer](const sta::LibertyCell* lhs,
                               const sta::LibertyCell* rhs) {
                      return resizer.bufferDriveResistance(lhs)
                             < resizer.bufferDriveResistance(rhs);
                    });
  for (sta::LibertyCell* cell : swappable) {
    if (cell == nullptr
        || std::find(cells.begin(), cells.end(), cell) != cells.end()) {
      continue;
    }
    cells.push_back(cell);
    if (static_cast<int>(cells.size()) >= max_cells) {
      break;
    }
  }
  return cells;
}

std::string splitSignature(Resizer& resizer,
                           sta::LibertyCell* buffer_cell,
                           const sta::PinSet& load_pins)
{
  std::vector<std::string> names;
  names.reserve(load_pins.size());
  for (const sta::Pin* load_pin : load_pins) {
    names.push_back(resizer.network()->pathName(load_pin));
  }
  std::ranges::sort(names);
  std::string signature = buffer_cell != nullptr ? buffer_cell->name() : "";
  for (const std::string& name : names) {
    signature += "|";
    signature += name;
  }
  return signature;
}

}  // namespace

SplitLoadGenerator::SplitLoadGenerator(const GeneratorContext& context)
    : MoveGenerator(context)
{
}

std::vector<std::unique_ptr<MoveCandidate>> SplitLoadGenerator::generate(
    const Target& target)
{
  std::vector<std::unique_ptr<MoveCandidate>> candidates;
  sta::Pin* drvr_pin = nullptr;
  sta::Vertex* drvr_vertex = nullptr;
  if (!resolveDriverPin(resizer_, target, drvr_pin, drvr_vertex)) {
    return candidates;
  }
  if (resizer_.drivesSequentialClockPin(target.inst(resizer_))) {
    return candidates;
  }

  std::vector<FanoutSlack> fanout_slacks
      = collectRankedFanoutSlacks(resizer_, target, drvr_vertex);
  if (fanout_slacks.empty()) {
    return candidates;
  }

  odb::dbNet* db_drvr_net = nullptr;
  odb::dbModNet* db_mod_drvr_net = nullptr;
  resizer_.dbNetwork()->net(drvr_pin, db_drvr_net, db_mod_drvr_net);
  sta::Net* drvr_net = resizer_.dbNetwork()->dbToSta(db_drvr_net);
  if (drvr_net == nullptr) {
    return candidates;
  }

  const std::vector<int> counts = splitCounts(fanout_slacks.size());
  const std::vector<sta::LibertyCell*> buffer_cells = splitBufferCells(resizer_);
  const bool include_critical_branch
      = envFlag("RSZ_SPLIT_LOAD_CRITICAL_BRANCH_VARIANTS", true)
        && target.slack <= -9.0e-11f;
  std::vector<bool> modes = {false};
  if (include_critical_branch) {
    modes.push_back(true);
  }

  std::set<std::string> seen;
  for (const bool move_most_critical : modes) {
    for (const int count : counts) {
      for (sta::LibertyCell* buffer_cell : buffer_cells) {
        std::unique_ptr<sta::PinSet> load_pins
            = chooseSplitLoads(resizer_, fanout_slacks, count, move_most_critical);
        if (load_pins->empty()) {
          continue;
        }
        const std::string signature
            = splitSignature(resizer_, buffer_cell, *load_pins);
        if (!seen.insert(signature).second) {
          continue;
        }
        const odb::Point split_loc = computeSplitLocation(
            resizer_, drvr_pin, fanout_slacks, count, move_most_critical);
        candidates.push_back(std::make_unique<SplitLoadCandidate>(
            resizer_,
            target,
            drvr_net,
            buffer_cell,
            split_loc,
            std::move(load_pins)));
      }
    }
  }
  return candidates;
}

}  // namespace rsz
