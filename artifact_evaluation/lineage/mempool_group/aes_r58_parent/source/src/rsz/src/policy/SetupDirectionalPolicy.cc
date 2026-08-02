// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SetupDirectionalPolicy.hh"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "MoveCandidate.hh"
#include "MoveCommitter.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "RepairTargetCollector.hh"
#include "VtSwapGenerator.hh"
#include "est/EstimateParasitics.h"
#include "policy/OptimizationPolicy.hh"
#include "rsz/Resizer.hh"
#include "sta/Delay.hh"
#include "sta/Fuzzy.hh"
#include "sta/GraphClass.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/Path.hh"
#include "sta/PortDirection.hh"
#include "utl/Logger.h"
#include "utl/scope.h"
#include "utl/timer.h"

namespace rsz {

using std::pair;
using utl::RSZ;

namespace {
static constexpr int kDelayDigits = 3;
static constexpr int kDirectionalMinRepairBudget = 4;
static constexpr int kDirectionalMidRepairBudget = 6;
static constexpr int kDirectionalHighRepairBudget = 9;
static constexpr float kDirectionalMidSlack = -2.0e-10f;
static constexpr float kDirectionalHighSlack = -4.0e-10f;
static constexpr float kDirectionalWnsDamageCap = 4.0e-12f;
static constexpr float kDirectionalTnsExpandCapRatio = 0.015f;
static constexpr float kDirectionalTnsRatioFloor = 1.0e-12f;

bool directionalWnsNeedsCollateralGuard()
{
  const char* value = std::getenv("RSZ_DIRECTIONAL_WNS_NEEDS_TNS_GUARD");
  return value == nullptr || std::string(value) != "0";
}

int directionalRepairBudget(const sta::Slack point_slack,
                            const int configured_limit)
{
  int budget = kDirectionalMinRepairBudget;
  if (point_slack <= kDirectionalHighSlack) {
    budget = kDirectionalHighRepairBudget;
  } else if (point_slack <= kDirectionalMidSlack) {
    budget = kDirectionalMidRepairBudget;
  }
  if (configured_limit > 0) {
    budget = std::max(budget, configured_limit);
  }
  return budget;
}

int directionalPinLimit(const int repair_budget)
{
  return std::max(12, repair_budget * 4);
}

bool withinDirectionalCollateralGuard(const sta::Slack prev_wns,
                                      const sta::Slack new_wns,
                                      const sta::Slack prev_endpoint_tns,
                                      const sta::Slack new_endpoint_tns,
                                      const sta::Slack prev_startpoint_tns,
                                      const sta::Slack new_startpoint_tns)
{
  const float wns_damage
      = std::max(0.0f, static_cast<float>(prev_wns - new_wns));
  const float endpoint_tns_expand
      = std::max(0.0f,
                 std::abs(static_cast<float>(new_endpoint_tns))
                     - std::abs(static_cast<float>(prev_endpoint_tns)))
        / std::max(kDirectionalTnsRatioFloor,
                   std::abs(static_cast<float>(prev_endpoint_tns)));
  const float startpoint_tns_expand
      = std::max(0.0f,
                 std::abs(static_cast<float>(new_startpoint_tns))
                     - std::abs(static_cast<float>(prev_startpoint_tns)))
        / std::max(kDirectionalTnsRatioFloor,
                   std::abs(static_cast<float>(prev_startpoint_tns)));
  return wns_damage <= kDirectionalWnsDamageCap
         && endpoint_tns_expand <= kDirectionalTnsExpandCapRatio
         && startpoint_tns_expand <= kDirectionalTnsExpandCapRatio;
}

}  // namespace

void SetupDirectionalPolicy::iterate()
{
  buildMainMoveSequence(/*log_sequence=*/false);
  repairSetupDirectional(use_starts_,
                         config_.setup_slack_margin,
                         config_.max_passes,
                         config_.verbose);
  if (use_starts_) {
    committer_.printTrackerPhaseSummary(
        "STARTPOINT_FANOUT Phase Summary",
        "STARTPOINT_FANOUT Phase Startpoint Profiler",
        true);
  } else {
    committer_.printTrackerPhaseSummary(
        "ENDPOINT_FANIN Phase Summary",
        "ENDPOINT_FANIN Phase Endpoint Profiler",
        true);
  }
  markRunComplete(true);
}

void SetupDirectionalPolicy::repairSetupDirectional(
    const bool use_startpoints,
    const float setup_slack_margin,
    const int max_passes_per_point,
    const bool verbose)
{
  int& opto_iteration = setup_context_.iteration;
  const char phase_marker = phaseMarkerForIndex(setup_context_.phase_index);
  const char* phase_name
      = use_startpoints ? "STARTPOINT_FANOUT" : "ENDPOINT_FANIN";
  const utl::DebugScopedTimer timer(
      logger_,
      RSZ,
      "repair_setup",
      10,
      fmt::format("{}{} Phase Time: {{}}", phase_name, phase_marker));
  const char* point_type = use_startpoints ? "startpoint" : "endpoint";
  const char* point_type_cap = use_startpoints ? "Startpoint" : "Endpoint";
  committer_.capturePrePhaseSlack();
  const float margin_percentages[] = {0.20f, 0.50f, 1.00f};
  const sta::Slack diminishing_returns_threshold(1e-11f);

  debugPrint(logger_,
             RSZ,
             "repair_setup",
             1,
             "{}{} Phase: Iterative threshold refinement...",
             phase_name,
             phase_marker);
  target_collector_->collectViolatingPoints(use_startpoints);
  const int max_point_count
      = target_collector_->getMaxPointCount(use_startpoints);
  printProgress(opto_iteration, false, phase_marker, use_startpoints);
  if (max_point_count == 0) {
    debugPrint(logger_,
               RSZ,
               "repair_setup",
               1,
               "{}{} Phase: No violating {}s, exiting",
               phase_name,
               phase_marker,
               point_type);
    return;
  }

  debugPrint(logger_,
             RSZ,
             "repair_setup",
             1,
             "{}{} Phase: Processing {} violating {}s",
             phase_name,
             phase_marker,
             max_point_count,
             point_type);
  int points_processed = 0;

  for (int point_index = 0; point_index < max_point_count; point_index++) {
    if (point_index >= setup_context_.max_end_repairs) {
      debugPrint(logger_,
                 RSZ,
                 "repair_setup",
                 1,
                 "{}{} Phase: Hit maximum point repairs of {}",
                 phase_name,
                 phase_marker,
                 setup_context_.max_end_repairs);
      break;
    }

    target_collector_->setToPoint(point_index, use_startpoints);
    sta::Vertex* point = target_collector_->getCurrentPoint(use_startpoints);
    if (point == nullptr) {
      continue;
    }

    const sta::Pin* point_pin
        = target_collector_->getCurrentPointPin(use_startpoints);
    sta::Slack point_slack = sta_->slack(point, max_);
    if (sta::fuzzyGreaterEqual(point_slack, setup_slack_margin)) {
      debugPrint(logger_,
                 RSZ,
                 "repair_setup",
                 2,
                 "{}{} Phase: {} {} (index {}) already meets timing, "
                 "skipping",
                 phase_name,
                 phase_marker,
                 point_type_cap,
                 network_->pathName(point_pin),
                 point_index);
      continue;
    }

    debugPrint(logger_,
               RSZ,
               "repair_setup",
               1,
               "{}{} Phase: Processing {} {} (index {}), slack = {}",
               phase_name,
               phase_marker,
               point_type,
               network_->pathName(point_pin),
               point_index,
               delayAsString(point_slack, kDelayDigits, sta_));
    rejected_pin_moves_current_endpoint_.clear();
    committer_.setCurrentEndpoint(point_pin);
    int point_pass_count = 0;
    sta::Slack prev_point_slack = point_slack;

    for (float margin_pct : margin_percentages) {
      if (point_pass_count >= max_passes_per_point) {
        debugPrint(logger_,
                   RSZ,
                   "repair_setup",
                   1,
                   "{}{} Phase: {} {} reached pass limit {}, moving to next "
                   "{}",
                   phase_name,
                   phase_marker,
                   point_type_cap,
                   network_->pathName(point_pin),
                   max_passes_per_point,
                   point_type);
        break;
      }
      point_slack = sta_->slack(point, max_);
      if (sta::fuzzyGreaterEqual(point_slack, setup_slack_margin)) {
        debugPrint(logger_,
                   RSZ,
                   "repair_setup",
                   1,
                   "{}{} Phase: {} {} now meets timing, moving to next {}",
                   phase_name,
                   phase_marker,
                   point_type_cap,
                   network_->pathName(point_pin),
                   point_type);
        break;
      }

      const sta::Slack margin = std::abs(point_slack) * margin_pct;
      const sta::Slack threshold = point_slack + margin;
      const sta::Slack prev_wns = target_collector_->getWns();
      const sta::Slack prev_endpoint_tns = target_collector_->getTns(false);
      const sta::Slack prev_startpoint_tns = target_collector_->getTns(true);

      committer_.beginJournal();
      point_pass_count++;
      opto_iteration++;
      if (verbose || opto_iteration % print_interval_ == 0) {
        printProgress(opto_iteration, false, phase_marker, use_startpoints);
      }

      std::vector<const sta::Pin*> viol_pins
          = target_collector_->collectViolatorsByDirectionalTraversal(
              use_startpoints,
              point_index,
              threshold,
              rsz::ViolatorSortType::SORT_AND_FILTER_BY_HEURISTIC);
      const int repair_budget = directionalRepairBudget(
          point_slack, setup_context_.max_repairs_per_pass);
      const int pin_limit = directionalPinLimit(repair_budget);
      if (std::cmp_greater(viol_pins.size(), pin_limit)) {
        viol_pins.resize(pin_limit);
      }

      std::vector<std::pair<const sta::Pin*, MoveType>> chosen_moves;
      bool changed;
      {
        utl::SetAndRestore<int> override_max_repairs(
            setup_context_.max_repairs_per_pass, repair_budget);
        changed = repairPins(viol_pins,
                             nullptr,
                             &rejected_pin_moves_current_endpoint_,
                             &chosen_moves,
                             /*force_single_repair=*/false);
      }
      if (!changed) {
        committer_.commitJournal();
        continue;
      }

      estimate_parasitics_->updateParasitics();
      sta_->findRequireds();

      const sta::Slack new_point_slack = sta_->slack(point, max_);
      const sta::Slack new_wns = target_collector_->getWns();
      const sta::Slack new_endpoint_tns = target_collector_->getTns(false);
      const sta::Slack new_startpoint_tns = target_collector_->getTns(true);
      const bool wns_improved = sta::fuzzyGreater(new_wns, prev_wns);
      const bool wns_same = sta::fuzzyEqual(new_wns, prev_wns);
      const bool point_improved
          = sta::fuzzyGreater(new_point_slack, prev_point_slack);
      const bool endpoint_tns_improved
          = sta::fuzzyGreater(new_endpoint_tns, prev_endpoint_tns);
      const bool startpoint_tns_improved
          = sta::fuzzyGreater(new_startpoint_tns, prev_startpoint_tns);
      const bool focused_tns_improved
          = use_startpoints ? startpoint_tns_improved : endpoint_tns_improved;
      const bool collateral_ok = withinDirectionalCollateralGuard(
          prev_wns,
          new_wns,
          prev_endpoint_tns,
          new_endpoint_tns,
          prev_startpoint_tns,
          new_startpoint_tns);
      const bool wns_progress_ok
          = wns_improved
            && (!directionalWnsNeedsCollateralGuard() || collateral_ok
                || focused_tns_improved);
      const bool better
          = wns_progress_ok
            || (collateral_ok
                && (point_improved
                    || (wns_same && focused_tns_improved)));

      const sta::Slack improvement = new_point_slack - prev_point_slack;
      debugPrint(logger_,
                 RSZ,
                 "repair_setup",
                 1,
                 "{}{} Phase: Threshold {}/3: {} slack {} -> {} (imp: {}), "
                 "WNS {} -> {}, EnTNS {} -> {}, StTNS {} -> {}{}",
                 phase_name,
                 phase_marker,
                 point_pass_count,
                 point_type,
                 delayAsString(prev_point_slack, kDelayDigits, sta_),
                 delayAsString(new_point_slack, kDelayDigits, sta_),
                 delayAsString(improvement, kDelayDigits, sta_),
                 delayAsString(prev_wns, kDelayDigits, sta_),
                 delayAsString(new_wns, kDelayDigits, sta_),
                 delayAsString(prev_endpoint_tns, 1, sta_),
                 delayAsString(new_endpoint_tns, 1, sta_),
                 delayAsString(prev_startpoint_tns, 1, sta_),
                 delayAsString(new_startpoint_tns, 1, sta_),
                 better ? " [ACCEPT]" : " [REJECT]");
      if (better) {
        committer_.commitJournal();
        prev_point_slack = new_point_slack;
        if (improvement < diminishing_returns_threshold) {
          debugPrint(
              logger_,
              RSZ,
              "repair_setup",
              1,
              "{}{} Phase: Improvement {} < {} (diminishing returns), "
              "moving to next {}",
              phase_name,
              phase_marker,
              delayAsString(improvement, kDelayDigits, sta_),
              delayAsString(diminishing_returns_threshold, kDelayDigits, sta_),
              point_type);
          break;
        }
      } else {
        committer_.restoreJournal();
        for (const auto& [chosen_pin, chosen_type] : chosen_moves) {
          rejected_pin_moves_current_endpoint_[chosen_pin].insert(chosen_type);
        }
      }
    }
    points_processed++;
  }

  printProgress(opto_iteration, true, phase_marker, use_startpoints);
  if (logger_->debugCheck(RSZ, "repair_setup", 1)) {
    sta::Slack final_wns;
    sta::Vertex* final_worst;
    sta_->worstSlack(max_, final_wns, final_worst);
    const float final_tns = sta_->totalNegativeSlack(max_);
    debugPrint(logger_,
               RSZ,
               "repair_setup",
               1,
               "{}{} Phase complete. {}s processed: {}, WNS: {}, TNS: {}",
               phase_name,
               phase_marker,
               point_type_cap,
               points_processed,
               delayAsString(final_wns, kDelayDigits, sta_),
               delayAsString(final_tns, 1, sta_));
  }
}

}  // namespace rsz
