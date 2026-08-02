// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SizeUpMtCandidate.hh"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <initializer_list>
#include <limits>
#include <string>
#include <utility>

#include "DelayEstimator.hh"
#include "MoveCandidate.hh"
#include "OptimizerTypes.hh"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/PortDirection.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

namespace {

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

float inputCapOf(sta::LibertyCell* cell)
{
  if (cell == nullptr) {
    return 0.0f;
  }
  float cap = 0.0f;
  sta::LibertyCellPortIterator port_iter(cell);
  while (port_iter.hasNext()) {
    sta::LibertyPort* port = port_iter.next();
    if (port != nullptr && port->direction()->isAnyInput()) {
      cap += port->capacitance();
    }
  }
  return cap;
}

float growthRatio(const float before, const float after)
{
  return std::max(0.0f, after - before) / positiveOrOne(before);
}

bool envEnabled(const char* name, const bool default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) != "0";
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

bool hasToken(const std::string& name, const char* lower, const char* upper)
{
  return name.find(lower) != std::string::npos
         || name.find(upper) != std::string::npos;
}

bool hasAnyToken(const std::string& name,
                 const std::initializer_list<const char*> tokens)
{
  return std::any_of(tokens.begin(), tokens.end(), [&name](const char* token) {
    return name.find(token) != std::string::npos;
  });
}

int vtRank(const std::string& name)
{
  if (hasAnyToken(name, {"_SL", "_SLVT"})) {
    return 3;
  }
  if (hasAnyToken(name, {"_L", "_LVT"})) {
    return 2;
  }
  if (hasAnyToken(name, {"_R", "_RVT"})) {
    return 1;
  }
  return 0;
}

float weakDriverSizeupBonus(const Target& target,
                            sta::LibertyCell* current_cell,
                            sta::LibertyCell* replacement)
{
  if (!envEnabled("RSZ_SIZEUP_WEAK_DRIVER_SCORE", true)
      || current_cell == nullptr || replacement == nullptr) {
    return 0.0f;
  }
  const float slack_gate
      = envFloat("RSZ_SIZEUP_WEAK_DRIVER_SLACK", -1.5e-10f);
  if (target.slack > slack_gate) {
    return 0.0f;
  }

  const std::string current_name = current_cell->name();
  const std::string replacement_name = replacement->name();
  float bonus = 0.0f;
  if (hasToken(current_name, "xp33", "XP33")
      && !hasToken(replacement_name, "xp33", "XP33")) {
    bonus += envFloat("RSZ_SIZEUP_XP33_ESCAPE_BONUS", 9.0e-12f);
  } else if (hasToken(current_name, "xp5", "XP5")
             && !hasToken(replacement_name, "xp5", "XP5")
             && !hasToken(replacement_name, "xp33", "XP33")) {
    bonus += envFloat("RSZ_SIZEUP_XP5_ESCAPE_BONUS", 4.0e-12f);
  }
  const float very_critical_slack
      = envFloat("RSZ_SIZEUP_WEAK_DRIVER_STRONG_SLACK", -3.5e-10f);
  if (bonus > 0.0f && target.slack <= very_critical_slack) {
    bonus *= envFloat("RSZ_SIZEUP_WEAK_DRIVER_STRONG_MULT", 1.5f);
  }
  return bonus;
}

float innovusLikeCriticalBonus(const Target& target,
                               Resizer& resizer,
                               sta::LibertyCell* current_cell,
                               sta::LibertyCell* replacement)
{
  if (!envEnabled("RSZ_SIZEUP_INNOVUS_LIKE_CRITICAL", false)
      || current_cell == nullptr || replacement == nullptr) {
    return 0.0f;
  }
  const float slack_gate
      = envFloat("RSZ_SIZEUP_INNOVUS_LIKE_SLACK", -1.2e-10f);
  if (target.slack > slack_gate) {
    return 0.0f;
  }

  const std::string current_name = current_cell->name();
  const std::string replacement_name = replacement->name();
  float bonus = 0.0f;
  if (hasAnyToken(current_name, {"xp33", "XP33", "x1_", "X1_"})
      && hasAnyToken(replacement_name,
                     {"xp5", "XP5", "xp67", "XP67", "x1p5", "X1P5"})) {
    bonus += envFloat("RSZ_SIZEUP_INNOVUS_LIKE_MEDIUM_BONUS", 1.1e-11f);
  }
  if (vtRank(replacement_name) > vtRank(current_name)) {
    const float leakage_growth =
        growthRatio(leakageOf(resizer, current_cell),
                    leakageOf(resizer, replacement));
    const float vt_bonus_growth_limit =
        envFloat("RSZ_SIZEUP_INNOVUS_LIKE_VT_MAX_GROWTH", 0.75f);
    if (leakage_growth <= vt_bonus_growth_limit) {
      const float discount =
          std::clamp((vt_bonus_growth_limit - leakage_growth)
                         / std::max(0.05f, vt_bonus_growth_limit),
                     0.0f,
                     1.0f);
      bonus += envFloat("RSZ_SIZEUP_INNOVUS_LIKE_VT_BONUS", 1.5e-12f)
               * discount;
    } else {
      bonus -= envFloat("RSZ_SIZEUP_INNOVUS_LIKE_VT_OVER_PENALTY",
                        3.0e-12f)
               * std::min(leakage_growth, 5.0f);
    }
  }
  if (hasAnyToken(replacement_name,
                  {"x8", "X8", "x10", "X10", "x12", "X12", "x16", "X16",
                   "x24", "X24"})) {
    bonus -= envFloat("RSZ_SIZEUP_INNOVUS_LIKE_OVERSIZE_PENALTY", 1.4e-11f);
  }
  return bonus;
}

float mediumDriveBonus(sta::LibertyCell* current_cell,
                       sta::LibertyCell* replacement)
{
  if (!envEnabled("RSZ_SIZEUP_LOW_POWER_TIMING", false)
      || current_cell == nullptr || replacement == nullptr) {
    return 0.0f;
  }

  const std::string current_name = current_cell->name();
  const std::string replacement_name = replacement->name();
  float bonus = 0.0f;
  if (hasToken(current_name, "xp33", "XP33")
      && (hasToken(replacement_name, "xp5", "XP5")
          || hasToken(replacement_name, "xp67", "XP67")
          || hasToken(replacement_name, "x1", "X1"))) {
    bonus += envFloat("RSZ_SIZEUP_XP33_TO_MEDIUM_BONUS", 7.0e-12f);
  }
  if (hasToken(current_name, "_R", "_R")
      && (hasToken(replacement_name, "_L", "_L")
          || hasToken(replacement_name, "_SL", "_SL"))) {
    bonus += envFloat("RSZ_SIZEUP_RVT_SPEED_BONUS", 2.0e-12f);
  }
  return bonus;
}

float oversizePenalty(sta::LibertyCell* replacement, const Target& target)
{
  if (!envEnabled("RSZ_SIZEUP_LOW_POWER_TIMING", false)
      || replacement == nullptr) {
    return 0.0f;
  }
  const float slack_gate
      = envFloat("RSZ_SIZEUP_OVERSIZE_SLACK", -2.5e-10f);
  if (target.slack <= slack_gate) {
    return 0.0f;
  }

  const std::string name = replacement->name();
  if (hasToken(name, "x24", "X24") || hasToken(name, "x16", "X16")
      || hasToken(name, "x12", "X12") || hasToken(name, "x10", "X10")
      || hasToken(name, "x8", "X8") || hasToken(name, "x6", "X6")) {
    return envFloat("RSZ_SIZEUP_LARGE_PENALTY", 1.0e-11f);
  }
  if (hasToken(name, "x4", "X4") || hasToken(name, "x5", "X5")) {
    return envFloat("RSZ_SIZEUP_OVERSIZE_PENALTY", 7.0e-12f);
  }
  return 0.0f;
}

}  // namespace

SizeUpMtCandidate::SizeUpMtCandidate(Resizer& resizer,
                                     const Target& target,
                                     sta::Pin* drvr_pin,
                                     sta::Instance* inst,
                                     sta::LibertyCell* replacement,
                                     const ArcDelayState& arc_delay,
                                     const bool power_aware,
                                     const float power_penalty)
    : MoveCandidate(resizer, target),
      drvr_pin_(drvr_pin),
      inst_(inst),
      current_cell_(resizer.network()->libertyCell(inst)),
      replacement_(replacement),
      arc_delay_(arc_delay),
      power_aware_(power_aware),
      power_penalty_(power_penalty)
{
}

Estimate SizeUpMtCandidate::estimate()
{
  // Score one size-up replacement from immutable inputs prepared before the
  // worker pool runs candidate estimation.
  const DelayEstimate delay_est
      = DelayEstimator::estimate(arc_delay_, replacement_);
  if (!delay_est.legal) {
    return {.legal = false, .score = delay_est.arrival_impr};
  }

  float score = delay_est.arrival_impr;
  if (power_aware_) {
    const float leakage_ratio =
        growthRatio(leakageOf(resizer_, current_cell_),
                    leakageOf(resizer_, replacement_));
    const float area_ratio =
        growthRatio(current_cell_ != nullptr ? current_cell_->area() : 0.0f,
                    replacement_ != nullptr ? replacement_->area() : 0.0f);
    score -= power_penalty_ * (leakage_ratio + 0.35f * area_ratio);
  }
  if (power_aware_) {
    const float cap_ratio =
        growthRatio(inputCapOf(current_cell_), inputCapOf(replacement_));
    const float cap_penalty
        = envFloat("RSZ_SIZEUP_INPUT_CAP_PENALTY", 0.0f);
    if (cap_penalty > 0.0f) {
      const float fanout_scale =
          std::sqrt(static_cast<float>(std::max(1, target_.fanout)));
      score -= cap_penalty * std::min(cap_ratio, 6.0f)
               * std::min(fanout_scale, 4.0f);
    }
  }
  score += weakDriverSizeupBonus(target_, current_cell_, replacement_);
  score += innovusLikeCriticalBonus(
      target_, resizer_, current_cell_, replacement_);
  score += mediumDriveBonus(current_cell_, replacement_);
  score -= oversizePenalty(replacement_, target_);
  return {.legal = score > 0.0f, .score = score};
}

MoveResult SizeUpMtCandidate::apply()
{
  // Re-validate max-cap against state mutated earlier in this commit batch.
  // Candidates were screened on a pre-batch snapshot, so two accepted
  // size-ups on different sinks of the same fanin net could each pass
  // independently yet combine past the driver's max-cap limit.
  if (!resizer_.replacementPreservesMaxCap(inst_, replacement_)) {
    debugPrint(resizer_.logger(),
               RSZ,
               "opt_moves",
               1,
               "REJECT size_up_mt1 {}: {} -> {} max-cap re-check failed",
               logName(),
               current_cell_->name(),
               replacement_->name());
    return {
        .accepted = false,
        .type = MoveType::kSizeUp,
        .touched_instances = {},
    };
  }

  const bool accepted = resizer_.replaceCell(inst_, replacement_);
  if (accepted) {
    const std::string& current_cell_name = current_cell_->name();
    const std::string& replacement_name = replacement_->name();
    debugPrint(resizer_.logger(),
               RSZ,
               "opt_moves",
               1,
               "ACCEPT size_up_mt1 {}: {} -> {}",
               logName(),
               current_cell_name,
               replacement_name);
    return {
        .accepted = true,
        .type = MoveType::kSizeUp,
        .move_count = 1,
        .touched_instances = {inst_},
    };
  }

  debugPrint(resizer_.logger(),
             RSZ,
             "opt_moves",
             1,
             "REJECT size_up_mt1 {}: {} -> {} replace failed",
             logName(),
             current_cell_->name(),
             replacement_->name());
  return {
      .accepted = false,
      .type = MoveType::kSizeUp,
      .touched_instances = {},
  };
}

std::string SizeUpMtCandidate::logName() const
{
  return drvr_pin_ != nullptr ? resizer_.network()->pathName(drvr_pin_) : "";
}

}  // namespace rsz
