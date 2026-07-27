// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SetupCritVtSwapPolicy.hh"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "MoveCommitter.hh"
#include "OptimizerTypes.hh"
#include "RepairTargetCollector.hh"
#include "VtSwapCandidate.hh"
#include "est/EstimateParasitics.h"
#include "rsz/Resizer.hh"
#include "sta/Fuzzy.hh"
#include "sta/GraphClass.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/Path.hh"
#include "sta/PortDirection.hh"
#include "utl/Logger.h"
#include "utl/env.h"
#include "utl/timer.h"

namespace rsz {

using std::pair;
using utl::RSZ;

namespace {
static constexpr size_t kMaxCritEndpoints = 100;
static constexpr int kMaxCritInstancesPerEndpoint = 50;
static constexpr float kCritVtSoftGrowthLimit = 1.0f;
static constexpr float kCritVtModerateGrowthLimit = 2.5f;
static constexpr float kCritVtHardGrowthLimit = 8.0f;
static constexpr float kCritVtHardSlack = -3.0e-10f;
static constexpr float kCritVtTnsDamageCapRatio = 0.01f;
static constexpr float kCritVtWnsDamageCap = 3.0e-12f;
static constexpr float kCritVtRatioFloor = 1.0e-12f;

bool readCritFlagEnv(const char* name, const bool default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) != "0";
}

float readCritFloatEnv(const char* name, const float default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  size_t parsed_chars = 0;
  const float parsed = std::stof(value, &parsed_chars);
  if (parsed_chars != std::string(value).size()) {
    throw std::runtime_error(std::string("Environment variable ") + name
                             + " must be a floating-point value.");
  }
  return parsed;
}

int readCritIntEnv(const char* name, const int default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  char* end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  if (end == value || *end != '\0') {
    return default_value;
  }
  return static_cast<int>(parsed);
}

float leakageOf(Resizer& resizer, sta::LibertyCell* cell)
{
  if (cell == nullptr) {
    return 0.0f;
  }
  return resizer.cellLeakage(cell).value_or(0.0f);
}

float positiveOrOne(const float value)
{
  return value > 0.0f && std::isfinite(value) ? value : 1.0f;
}

float growthRatio(const float before, const float after)
{
  return after / positiveOrOne(before);
}

bool legalTimingVtCell(Resizer& resizer,
                       sta::Instance* inst,
                       sta::LibertyCell* current_cell,
                       sta::LibertyCell* candidate_cell)
{
  return inst != nullptr && candidate_cell != nullptr
         && candidate_cell != current_cell && !resizer.dontUse(candidate_cell)
         && !candidate_cell->hasSequentials()
         && !candidate_cell->isClockCell()
         && !candidate_cell->isClockGate()
         && resizer.replacementPreservesMaxCap(inst, candidate_cell);
}

}  // namespace

void SetupCritVtSwapPolicy::iterate()
{
  int& num_viols = setup_context_.violation_count;
  // Critical VT swap runs as a separate phase because it is endpoint-agnostic.
  if (config_.skip_crit_vt_swap || config_.skip_vt_swap || !hasVtSwapCells()) {
    markRunComplete(true);
    return;
  }
  committer_.capturePrePhaseSlack();
  if (swapVTCritCells(num_viols)) {
    estimate_parasitics_->updateParasitics();
    sta_->findRequireds();
  }
  committer_.printTrackerPhaseSummary("VT Swap Phase Summary", nullptr, false);
  markRunComplete(true);
}

bool SetupCritVtSwapPolicy::swapVTCritCells(int& num_viols)
{
  bool changed = false;
  ViolatingEnds violating_ends
      = collectViolatingEndpoints(config_.setup_slack_margin);
  const int max_endpoints = std::max(
      1, readCritIntEnv("RSZ_CRIT_VT_MAX_ENDPOINTS", kMaxCritEndpoints));
  if (violating_ends.size() > static_cast<size_t>(max_endpoints)) {
    violating_ends.resize(max_endpoints);
  }
  // Collect a bounded fanin cone across the worst endpoints, then VT-swap that
  // deduplicated instance set in one committer batch.
  std::unordered_map<sta::Instance*, float> crit_insts;
  std::unordered_set<sta::Vertex*> visited;
  std::unordered_set<sta::Instance*> notSwappable;
  for (const auto& [endpoint, slack] : violating_ends) {
    traverseFaninCone(endpoint, crit_insts, visited, notSwappable);
  }
  debugPrint(logger_,
             RSZ,
             "swap_crit_vt",
             1,
             "identified {} critical instances",
             crit_insts.size());

  if (readCritFlagEnv("RSZ_CRIT_VT_LEGACY_BATCH", true)) {
    for (const std::pair<sta::Instance* const, float>& crit_inst_slack :
         crit_insts) {
      sta::Instance* inst = crit_inst_slack.first;
      sta::LibertyCell* best_cell = nullptr;
      if (!resizer_.checkAndMarkVTSwappable(inst, notSwappable, best_cell)) {
        continue;
      }
      sta::LibertyCell* current_cell = network_->libertyCell(inst);
      if (current_cell == nullptr || current_cell->hasSequentials()
          || current_cell->isClockCell() || current_cell->isClockGate()) {
        continue;
      }

      sta::Pin* output_pin = nullptr;
      if (committer_.moveTrackerEnabled(2)) {
        output_pin = outputPin(inst);
      }

      Target target;
      target.views = kInstanceView;
      target.driver_pin = output_pin;
      target.slack = crit_inst_slack.second;

      if (committer_.moveTrackerEnabled(2)) {
        committer_.setCurrentEndpoint(output_pin);
        committer_.trackViolatorWithTimingInfo(output_pin,
                                               graph_->pinDrvrVertex(output_pin),
                                               target.slack,
                                               *target_collector_);
      }

      VtSwapCandidate candidate(
          resizer_, target, output_pin, inst, current_cell, best_cell);
      committer_.trackMoveAttempt(output_pin, MoveType::kVtSwap);
      const MoveResult result = committer_.commit(candidate);
      if (result.accepted) {
        changed = true;
        debugPrint(logger_,
                   RSZ,
                   "swap_crit_vt",
                   1,
                   "inst {} did legacy crit VT swap",
                   network_->pathName(inst));
      }
    }
    if (changed) {
      committer_.acceptPendingMoves();
      estimate_parasitics_->updateParasitics();
      sta_->findRequireds();
      num_viols = collectViolatingEndpoints(config_.setup_slack_margin).size();
    } else {
      committer_.rejectPendingMoves();
    }

    return changed;
  }

  const int env_cap = utl::readEnvarNonNegativeInt("RSZ_CRIT_VT_MAX_MOVES", 0);
  const int max_moves = env_cap > 0 ? env_cap : policy_config_.max_committed_moves;
  const float min_score
      = readCritFloatEnv("RSZ_CRIT_VT_MIN_SCORE",
                         -std::numeric_limits<float>::infinity());
  const bool guard_enabled = readCritFlagEnv("RSZ_CRIT_VT_BATCH_GUARD", false);
  const float initial_tns = sta_->totalNegativeSlack(max_);
  sta::Slack initial_wns = 0.0;
  sta::Vertex* initial_worst = nullptr;
  sta_->worstSlack(max_, initial_wns, initial_worst);

  std::vector<CritVtCandidate> candidates;
  candidates.reserve(crit_insts.size());
  for (const std::pair<sta::Instance* const, float>& crit_inst_slack :
       crit_insts) {
    sta::Instance* inst = crit_inst_slack.first;
    sta::LibertyCell* best_cell = nullptr;
    if (!resizer_.checkAndMarkVTSwappable(inst, notSwappable, best_cell)) {
      continue;
    }
    sta::LibertyCell* current_cell = network_->libertyCell(inst);
    if (current_cell == nullptr || current_cell->hasSequentials()
        || current_cell->isClockCell() || current_cell->isClockGate()) {
      continue;
    }
    best_cell = selectCritVtCell(
        inst, current_cell, best_cell, crit_inst_slack.second);
    if (best_cell == nullptr || best_cell == current_cell) {
      debugPrint(logger_,
                 RSZ,
                 "swap_crit_vt",
                 2,
                 "skip crit VT swap {}: slack {} has no guarded speedup cell",
                 network_->pathName(inst),
                 crit_inst_slack.second);
      continue;
    }

    sta::Pin* output_pin = outputPin(inst);
    const float score
        = scoreCandidate(current_cell, best_cell, crit_inst_slack.second);
    if (score < min_score) {
      debugPrint(logger_,
                 RSZ,
                 "swap_crit_vt",
                 2,
                 "skip crit VT swap {}: score {} below min {}",
                 network_->pathName(inst),
                 score,
                 min_score);
      continue;
    }
    candidates.push_back({.inst = inst,
                          .output_pin = output_pin,
                          .current_cell = current_cell,
                          .best_cell = best_cell,
                          .slack = crit_inst_slack.second,
                          .score = score});
  }

  std::ranges::sort(candidates,
                    [](const CritVtCandidate& lhs,
                       const CritVtCandidate& rhs) {
                      return lhs.score > rhs.score;
                    });

  int committed = 0;
  if (guard_enabled) {
    committer_.beginJournal();
  }
  for (const CritVtCandidate& scored : candidates) {
    if (max_moves > 0 && committed >= max_moves) {
      break;
    }

    Target target;
    target.views = kInstanceView;
    target.driver_pin = scored.output_pin;
    target.slack = scored.slack;

    if (committer_.moveTrackerEnabled(2)) {
      committer_.setCurrentEndpoint(scored.output_pin);
      committer_.trackViolatorWithTimingInfo(scored.output_pin,
                                             graph_->pinDrvrVertex(scored.output_pin),
                                             target.slack,
                                             *target_collector_);
    }

    VtSwapCandidate candidate(
        resizer_, target, scored.output_pin, scored.inst, scored.current_cell, scored.best_cell);
    committer_.trackMoveAttempt(scored.output_pin, MoveType::kVtSwap);
    const MoveResult result = committer_.commit(candidate);
    if (result.accepted) {
      changed = true;
      ++committed;
      debugPrint(logger_,
                 RSZ,
                 "swap_crit_vt",
                 1,
                 "inst {} did crit VT swap score {}",
                 network_->pathName(scored.inst),
                 scored.score);
    }
  }
  if (changed) {
    estimate_parasitics_->updateParasitics();
    sta_->findRequireds();
    const float final_tns = sta_->totalNegativeSlack(max_);
    sta::Slack final_wns = 0.0;
    sta::Vertex* final_worst = nullptr;
    sta_->worstSlack(max_, final_wns, final_worst);
    const bool tns_improved = sta::fuzzyGreater(final_tns, initial_tns);
    const bool wns_improved = sta::fuzzyGreater(final_wns, initial_wns);
    const float tns_damage_ratio =
        std::max(0.0f, std::abs(final_tns) - std::abs(initial_tns))
        / std::max(kCritVtRatioFloor, std::abs(initial_tns));
    const float wns_damage =
        std::max(0.0f, static_cast<float>(initial_wns - final_wns));
    const bool guarded_accept =
        !guard_enabled || tns_improved
        || (wns_improved && tns_damage_ratio <= kCritVtTnsDamageCapRatio
            && wns_damage <= kCritVtWnsDamageCap);
    if (guarded_accept) {
      if (guard_enabled) {
        committer_.commitJournal();
      } else {
        committer_.acceptPendingMoves();
      }
      debugPrint(logger_,
                 RSZ,
                 "swap_crit_vt",
                 1,
                 "accepted crit VT batch moves={} tns {} -> {} wns {} -> {}",
                 committed,
                 delayAsString(initial_tns, 1, sta_),
                 delayAsString(final_tns, 1, sta_),
                 delayAsString(initial_wns, 3, sta_),
                 delayAsString(final_wns, 3, sta_));
    } else {
      if (guard_enabled) {
        committer_.restoreJournal();
      } else {
        committer_.rejectPendingMoves();
      }
      changed = false;
      debugPrint(logger_,
                 RSZ,
                 "swap_crit_vt",
                 1,
                 "rejected crit VT batch moves={} tns {} -> {} wns {} -> {}",
                 committed,
                 delayAsString(initial_tns, 1, sta_),
                 delayAsString(final_tns, 1, sta_),
                 delayAsString(initial_wns, 3, sta_),
                 delayAsString(final_wns, 3, sta_));
    }
  } else {
    if (guard_enabled) {
      committer_.restoreJournal();
    } else {
      committer_.rejectPendingMoves();
    }
  }

  if (changed) {
    num_viols = collectViolatingEndpoints(config_.setup_slack_margin).size();
  }

  return changed;
}

sta::LibertyCell* SetupCritVtSwapPolicy::selectCritVtCell(
    sta::Instance* inst,
    sta::LibertyCell* current_cell,
    sta::LibertyCell* fastest_cell,
    const float slack) const
{
  if (inst == nullptr || current_cell == nullptr || fastest_cell == nullptr
      || current_cell->hasSequentials() || current_cell->isClockCell()
      || current_cell->isClockGate()) {
    return nullptr;
  }
  if (!policy_config_.timing_power_aware_scoring) {
    return legalTimingVtCell(resizer_, inst, current_cell, fastest_cell)
               ? fastest_cell
               : nullptr;
  }

  const float current_leakage = leakageOf(resizer_, current_cell);
  const float fastest_leakage = leakageOf(resizer_, fastest_cell);
  if (fastest_leakage <= current_leakage
      && legalTimingVtCell(resizer_, inst, current_cell, fastest_cell)) {
    return fastest_cell;
  }

  const float growth_slack =
      readCritFloatEnv("RSZ_CRIT_VT_GROWTH_SLACK",
                       policy_config_.timing_vt_growth_slack);
  if (slack <= growth_slack
      && legalTimingVtCell(resizer_, inst, current_cell, fastest_cell)) {
    return fastest_cell;
  }

  const float soft_growth_limit = readCritFloatEnv(
      "RSZ_CRIT_VT_SOFT_GROWTH_LIMIT", kCritVtSoftGrowthLimit);
  if (slack < config_.setup_slack_margin
      && growthRatio(current_leakage, fastest_leakage) <= soft_growth_limit
      && legalTimingVtCell(resizer_, inst, current_cell, fastest_cell)) {
    return fastest_cell;
  }

  if (!readCritFlagEnv("RSZ_CRIT_VT_ENUMERATE_EQUIV", true)
      || !sta::fuzzyLess(slack, config_.setup_slack_margin)) {
    return nullptr;
  }

  const float hard_slack
      = readCritFloatEnv("RSZ_CRIT_VT_HARD_SLACK", kCritVtHardSlack);
  const bool hard_endpoint = slack <= hard_slack;
  const float growth_limit =
      hard_endpoint ? readCritFloatEnv("RSZ_CRIT_VT_HARD_GROWTH_LIMIT",
                                       kCritVtHardGrowthLimit)
                    : readCritFloatEnv("RSZ_CRIT_VT_MODERATE_GROWTH_LIMIT",
                                       kCritVtModerateGrowthLimit);
  const bool prefer_fast =
      hard_endpoint || !readCritFlagEnv("RSZ_CRIT_VT_PREFER_MODERATE", true);

  sta::LibertyCell* selected = nullptr;
  float selected_leakage = prefer_fast ? -std::numeric_limits<float>::infinity()
                                       : std::numeric_limits<float>::infinity();
  for (sta::LibertyCell* cell : resizer_.getVTEquivCells(current_cell)) {
    if (!legalTimingVtCell(resizer_, inst, current_cell, cell)) {
      continue;
    }
    const float cell_leakage = leakageOf(resizer_, cell);
    if (cell_leakage <= current_leakage) {
      return cell;
    }
    if (growthRatio(current_leakage, cell_leakage) > growth_limit) {
      continue;
    }
    if (selected == nullptr
        || (prefer_fast ? cell_leakage > selected_leakage
                        : cell_leakage < selected_leakage)) {
      selected = cell;
      selected_leakage = cell_leakage;
    }
  }

  return selected;
}

float SetupCritVtSwapPolicy::scoreCandidate(sta::LibertyCell* current_cell,
                                            sta::LibertyCell* best_cell,
                                            const float slack) const
{
  const float criticality = std::max(0.0f, -slack);
  if (!policy_config_.timing_power_aware_scoring) {
    return criticality;
  }

  const float current_leakage = leakageOf(resizer_, current_cell);
  const float leakage_growth =
      std::max(0.0f, leakageOf(resizer_, best_cell) - current_leakage);
  const float leakage_ratio = leakage_growth / positiveOrOne(current_leakage);
  const float low_cost_speedup_bonus =
      leakage_ratio <= readCritFloatEnv("RSZ_CRIT_VT_LOW_COST_RATIO", 1.5f)
          ? readCritFloatEnv("RSZ_CRIT_VT_LOW_COST_BONUS", 1.0e-11f)
          : 0.0f;
  return criticality + low_cost_speedup_bonus
         - policy_config_.timing_vt_power_penalty * leakage_ratio;
}

/*
 * The legacy score is kept above this comment in behavior but now gives a
 * small deterministic tie-break to low leakage-growth speedups.  That helps
 * the measured/full-flow stages spend the aggressive fastest-VT swaps on the
 * worst endpoints and still use moderate R->L style swaps on the tail.
 */

void SetupCritVtSwapPolicy::traverseFaninCone(
    sta::Vertex* endpoint,
    std::unordered_map<sta::Instance*, float>& crit_insts,
    std::unordered_set<sta::Vertex*>& visited,
    std::unordered_set<sta::Instance*>& notSwappable)
{
  if (visited.contains(endpoint)) {
    return;
  }

  visited.insert(endpoint);
  std::queue<sta::Vertex*> queue;
  queue.push(endpoint);
  int endpoint_insts = 0;
  const int max_instances_per_endpoint =
      std::max(1,
               readCritIntEnv("RSZ_CRIT_VT_MAX_INSTS_PER_ENDPOINT",
                              kMaxCritInstancesPerEndpoint));
  sta::LibertyCell* best_lib_cell;

  // Walk backward only through violating fanin logic and cap the number of
  // instances contributed by each endpoint.
  while (!queue.empty() && endpoint_insts < max_instances_per_endpoint) {
    sta::Vertex* current = queue.front();
    queue.pop();

    sta::Pin* pin = current->pin();
    sta::Instance* inst = network_->instance(pin);

    if (inst) {
      if (resizer_.checkAndMarkVTSwappable(inst, notSwappable, best_lib_cell)) {
        const sta::Slack inst_slack = getInstanceSlack(inst);
        if (sta::fuzzyLess(inst_slack, config_.setup_slack_margin)) {
          auto it = crit_insts.find(inst);
          if (it == crit_insts.end()) {
            crit_insts[inst] = inst_slack;
            endpoint_insts++;
            debugPrint(logger_,
                       RSZ,
                       "swap_crit_vt",
                       1,
                       "swapVTCritCells: found crit inst {}: slack {}",
                       network_->name(inst),
                       float(inst_slack));
          }
        }
      }
    }

    sta::VertexInEdgeIterator edge_iter(current, graph_);
    while (edge_iter.hasNext()) {
      sta::Edge* edge = edge_iter.next();
      sta::Vertex* fanin_vertex = edge->from(graph_);
      if (fanin_vertex->isRegClk()) {
        continue;
      }

      if (!visited.contains(fanin_vertex)) {
        const sta::Slack fanin_slack = sta_->slack(fanin_vertex, max_);
        if (sta::fuzzyLess(fanin_slack, config_.setup_slack_margin)) {
          queue.push(fanin_vertex);
          visited.insert(fanin_vertex);
        }
      }
    }
  }

  debugPrint(logger_,
             RSZ,
             "swap_crit_vt",
             1,
             "traverseFaninCone: endpoint {} has {} critical instances:",
             endpoint->name(network_),
             endpoint_insts);
  if (logger_->debugCheck(RSZ, "swap_crit_vt", 1)) {
    for (auto crit_inst_slack : crit_insts) {
      logger_->report(" {}", network_->pathName(crit_inst_slack.first));
    }
  }
}

sta::Slack SetupCritVtSwapPolicy::getInstanceSlack(sta::Instance* inst)
{
  sta::Slack worst_slack = std::numeric_limits<float>::max();
  sta::InstancePinIterator* pin_iter = network_->pinIterator(inst);
  while (pin_iter->hasNext()) {
    sta::Pin* pin = pin_iter->next();
    if (network_->direction(pin)->isAnyOutput()) {
      sta::Vertex* vertex = graph_->pinDrvrVertex(pin);
      if (vertex) {
        const sta::Slack pin_slack = sta_->slack(vertex, max_);
        worst_slack = std::min(worst_slack, pin_slack);
      }
    }
  }
  delete pin_iter;

  return worst_slack;
}

sta::Pin* SetupCritVtSwapPolicy::outputPin(sta::Instance* inst)
{
  sta::InstancePinIterator* pin_iter = network_->pinIterator(inst);
  while (pin_iter->hasNext()) {
    sta::Pin* pin = pin_iter->next();
    if (!network_->direction(pin)->isAnyOutput()) {
      continue;
    }

    delete pin_iter;
    return pin;
  }
  delete pin_iter;

  return nullptr;
}

}  // namespace rsz
