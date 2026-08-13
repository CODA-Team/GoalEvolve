// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "MeasuredVtSwapCandidate.hh"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string>

#include "MoveCandidate.hh"
#include "OptimizerTypes.hh"
#include "db_sta/dbSta.hh"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/Scene.hh"
#include "sta/Sta.hh"
#include "sta/Transition.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

namespace {

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

bool envEnabled(const char* name, const bool default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) != "0";
}

float positiveOrOne(const float value)
{
  return value > 0.0f && std::isfinite(value) ? value : 1.0f;
}

float leakageOf(Resizer& resizer, sta::LibertyCell* cell)
{
  if (cell == nullptr) {
    return 0.0f;
  }
  return resizer.cellLeakage(cell).value_or(0.0f);
}

float leakageGrowthRatio(Resizer& resizer,
                         sta::LibertyCell* current_cell,
                         sta::LibertyCell* candidate_cell)
{
  const float current_leakage = leakageOf(resizer, current_cell);
  const float candidate_leakage = leakageOf(resizer, candidate_cell);
  return std::max(0.0f, candidate_leakage - current_leakage)
         / positiveOrOne(current_leakage);
}

}  // namespace

MeasuredVtSwapCandidate::MeasuredVtSwapCandidate(
    Resizer& resizer,
    const Target& target,
    sta::Pin* driver_pin,
    sta::Instance* inst,
    sta::Vertex* driver_vertex,
    const sta::Scene* scene,
    sta::LibertyCell* current_cell,
    sta::LibertyCell* candidate_cell)
    : MoveCandidate(resizer, target),
      driver_pin_(driver_pin),
      inst_(inst),
      driver_vertex_(driver_vertex),
      scene_(scene),
      current_cell_(current_cell),
      candidate_cell_(candidate_cell)
{
}

Estimate MeasuredVtSwapCandidate::estimate()
{
  if (!resizer_.replacementPreservesMaxCap(inst_, candidate_cell_)) {
    return {.legal = false, .score = 0.0f};
  }

  // Evaluate the swap on a temporary journaled edit so the database can be
  // restored afterward.
  beginEstimateJournal();
  const float before_tns =
      resizer_.sta()->totalNegativeSlack(resizer_.maxAnalysisMode());
  const float before_wns =
      resizer_.sta()->worstSlack(resizer_.maxAnalysisMode());
  const float before_delay = arrivalDelay();
  if (!resizer_.replaceCell(inst_, candidate_cell_)) {
    restoreEstimateJournal(false);
    return {.legal = false, .score = 0.0f};
  }

  resizer_.updateParasiticsAndTiming();
  const float after_tns =
      resizer_.sta()->totalNegativeSlack(resizer_.maxAnalysisMode());
  const float after_wns =
      resizer_.sta()->worstSlack(resizer_.maxAnalysisMode());
  const float after_delay = arrivalDelay();
  restoreEstimateJournal(true);

  const float arrival_gain = before_delay - after_delay;
  const float tns_gain = after_tns - before_tns;
  const float wns_gain = after_wns - before_wns;
  const float leakage_growth =
      leakageGrowthRatio(resizer_, current_cell_, candidate_cell_);

  const float min_tns_gain =
      envFloat("RSZ_MEASURED_VT_MIN_TNS_GAIN", 2.0e-13f);
  const float min_wns_gain =
      envFloat("RSZ_MEASURED_VT_MIN_WNS_GAIN", 1.0e-13f);
  const float max_leakage_growth =
      envFloat("RSZ_MEASURED_VT_MAX_LEAK_GROWTH", 5.0f);
  const float leakage_penalty =
      envFloat("RSZ_MEASURED_VT_LEAK_PENALTY", 4.0e-13f);
  const float wns_weight =
      envFloat("RSZ_MEASURED_VT_WNS_WEIGHT", 0.35f);
  const float arrival_weight =
      envFloat("RSZ_MEASURED_VT_ARRIVAL_WEIGHT", 0.10f);
  const bool require_global_gain =
      envEnabled("RSZ_MEASURED_VT_REQUIRE_GLOBAL_GAIN", true);

  if (require_global_gain && tns_gain < min_tns_gain
      && wns_gain < min_wns_gain) {
    return {.legal = false,
            .score = tns_gain + wns_weight * wns_gain
                     + arrival_weight * arrival_gain};
  }
  if (leakage_growth > max_leakage_growth
      && tns_gain < min_tns_gain * (1.0f + leakage_growth)) {
    return {.legal = false,
            .score = tns_gain - leakage_penalty * leakage_growth};
  }

  const float score = tns_gain + wns_weight * std::max(0.0f, wns_gain)
                      + arrival_weight * std::max(0.0f, arrival_gain)
                      - leakage_penalty * leakage_growth;
  return {.legal = score > 0.0f, .score = score};
}

MoveResult MeasuredVtSwapCandidate::apply()
{
  // Apply the chosen VT replacement after the one-stage policy commits it.
  if (!resizer_.replaceCell(inst_, candidate_cell_)) {
    debugPrint(resizer_.logger(),
               RSZ,
               "opt_moves",
               1,
               "REJECT measured_vt_swap {}: {} -> {} swap failed",
               logName(),
               current_cell_->name(),
               candidate_cell_->name());
    return {
        .accepted = false,
        .type = MoveType::kVtSwap,
        .touched_instances = {},
    };
  }

  debugPrint(resizer_.logger(),
             RSZ,
             "opt_moves",
             1,
             "ACCEPT measured_vt_swap {}: {} -> {}",
             logName(),
             current_cell_->name(),
             candidate_cell_->name());
  return {
      .accepted = true,
      .type = MoveType::kVtSwap,
      .move_count = 1,
      .touched_instances = {inst_},
  };
}

std::string MeasuredVtSwapCandidate::logName() const
{
  return resizer_.network()->pathName(driver_pin_);
}

float MeasuredVtSwapCandidate::arrivalDelay() const
{
  const sta::SceneSeq scenes
      = resizer_.sta()->makeSceneSeq(const_cast<sta::Scene*>(scene_));
  return resizer_.sta()->arrival(driver_vertex_,
                                 sta::RiseFallBoth::riseFall(),
                                 scenes,
                                 resizer_.maxAnalysisMode());
}

void MeasuredVtSwapCandidate::beginEstimateJournal() const
{
  odb::dbDatabase::beginEco(resizer_.block());
}

void MeasuredVtSwapCandidate::restoreEstimateJournal(
    const bool had_changes) const
{
  resizer_.initForJournalRestore();
  odb::dbDatabase::undoEco(resizer_.block());
  if (had_changes) {
    resizer_.updateParasiticsAndTiming();
  }
}

}  // namespace rsz
