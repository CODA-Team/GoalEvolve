// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "VtSwapMtCandidate.hh"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string>
#include <vector>

#include "DelayEstimator.hh"
#include "MoveCandidate.hh"
#include "OptimizerTypes.hh"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

namespace {

Estimate makeRejectedEstimate(const float score = 0.0f)
{
  return {.legal = false, .score = score};
}

Estimate makeAcceptedEstimate(const float score)
{
  return {.legal = true, .score = score};
}

const char* applyLogFormat(const bool accepted)
{
  return accepted ? "ACCEPT vt_swap_mt1 {}: {} -> {}"
                  : "REJECT vt_swap_mt1 {}: {} -> {} swap failed";
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

bool hasToken(const std::string& name, const char* token)
{
  return name.find(token) != std::string::npos;
}

int vtRank(const std::string& name)
{
  if (hasToken(name, "_SL") || hasToken(name, "_SLVT")) {
    return 3;
  }
  if (hasToken(name, "_L") || hasToken(name, "_LVT")) {
    return 2;
  }
  if (hasToken(name, "_R") || hasToken(name, "_RVT")) {
    return 1;
  }
  return 0;
}

float criticalVtSpeedBonus(const Target& target,
                           Resizer& resizer,
                           sta::LibertyCell* current_cell,
                           sta::LibertyCell* candidate_cell)
{
  if (current_cell == nullptr || candidate_cell == nullptr) {
    return 0.0f;
  }
  const float slack_gate = envFloat("RSZ_MT_VT_SPEEDUP_SLACK", -2.2e-10f);
  if (target.slack > slack_gate) {
    return 0.0f;
  }
  const int current_rank = vtRank(current_cell->name());
  const int candidate_rank = vtRank(candidate_cell->name());
  if (candidate_rank <= current_rank) {
    return 0.0f;
  }
  const float growth_ratio
      = leakageGrowthRatio(resizer, current_cell, candidate_cell);
  const float free_growth
      = envFloat("RSZ_MT_VT_SPEEDUP_FREE_GROWTH", 0.65f);
  const float max_bonus_growth
      = envFloat("RSZ_MT_VT_SPEEDUP_BONUS_MAX_GROWTH", 1.8f);
  if (growth_ratio > max_bonus_growth) {
    return 0.0f;
  }

  const float base = envFloat("RSZ_MT_VT_SPEEDUP_BONUS", 2.0e-12f);
  const float criticality
      = std::clamp(-static_cast<float>(target.slack) / 1.0e-10f, 1.0f, 4.0f);
  const float growth_discount
      = std::clamp((max_bonus_growth - growth_ratio)
                       / std::max(0.05f, max_bonus_growth - free_growth),
                   0.0f,
                   1.0f);
  return base * criticality * (candidate_rank - current_rank)
         * growth_discount;
}

}  // namespace

VtSwapMtCandidate::VtSwapMtCandidate(Resizer& resizer,
                                     const Target& target,
                                     sta::Pin* driver_pin,
                                     sta::Instance* inst,
                                     sta::LibertyCell* current_cell,
                                     sta::LibertyCell* candidate_cell,
                                     const ArcDelayState& arc_delay,
                                     const bool power_aware,
                                     const float power_penalty)
    : MoveCandidate(resizer, target),
      driver_pin_(driver_pin),
      inst_(inst),
      current_cell_(current_cell),
      candidate_cell_(candidate_cell),
      arc_delay_(arc_delay),
      power_aware_(power_aware),
      power_penalty_(power_penalty)
{
}

Estimate VtSwapMtCandidate::estimate()
{
  // Reuse the pre-built local delay context to score the candidate without
  // mutating the design.
  if (!resizer_.replacementPreservesMaxCap(inst_, candidate_cell_)) {
    return makeRejectedEstimate();
  }

  const DelayEstimate delay_est
      = DelayEstimator::estimate(arc_delay_, candidate_cell_);
  if (!delay_est.legal) {
    // Preserve the computed score for non-improving swaps so policy ranking can
    // still compare rejected candidates consistently.
    if (delay_est.reason == FailReason::kEstimateNonImproving) {
      return makeRejectedEstimate(delay_est.arrival_impr);
    }
    return makeRejectedEstimate();
  }
  float score = delay_est.arrival_impr;
  if (power_aware_) {
    const float current_leakage = leakageOf(resizer_, current_cell_);
    const float leakage_growth =
        std::max(0.0f, leakageOf(resizer_, candidate_cell_) - current_leakage);
    score -= power_penalty_ * leakage_growth / positiveOrOne(current_leakage);
    const float extra_vt_growth_penalty
        = envFloat("RSZ_MT_VT_SPEEDUP_GROWTH_PENALTY", 1.4e-12f);
    const float growth_ratio
        = leakage_growth / positiveOrOne(current_leakage);
    score -= extra_vt_growth_penalty * std::min(growth_ratio, 8.0f);
  }
  score += criticalVtSpeedBonus(
      target_, resizer_, current_cell_, candidate_cell_);
  if (score <= 0.0f) {
    return makeRejectedEstimate(score);
  }
  return makeAcceptedEstimate(score);
}

MoveResult VtSwapMtCandidate::apply()
{
  // Apply the chosen VT replacement after the policy has selected the best
  // score.
  const bool accepted = resizer_.replaceCell(inst_, candidate_cell_);
  debugPrint(resizer_.logger(),
             RSZ,
             "opt_moves",
             1,
             applyLogFormat(accepted),
             getDrvPinName(),
             current_cell_->name(),
             candidate_cell_->name());
  return {
      .accepted = accepted,
      .type = MoveType::kVtSwap,
      .move_count = accepted ? 1 : 0,
      .touched_instances = accepted ? std::vector<sta::Instance*>{inst_}
                                    : std::vector<sta::Instance*>{},
  };
}

std::string VtSwapMtCandidate::getDrvPinName() const
{
  return resizer_.network()->pathName(driver_pin_);
}

}  // namespace rsz
