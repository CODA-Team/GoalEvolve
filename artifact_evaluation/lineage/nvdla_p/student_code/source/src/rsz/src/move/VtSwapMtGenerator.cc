// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "VtSwapMtGenerator.hh"

#include <cstddef>
#include <cstdlib>
#include <limits>
#include <memory>
#include <string>
#include <vector>

#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "VtSwapMtCandidate.hh"
#include "db_sta/dbNetwork.hh"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"

namespace rsz {

namespace {

constexpr float kMtVtCriticalSpeedupSlack = -1.4e-11f;
constexpr float kMtVtModerateGrowthLimit = 2.2f;
constexpr float kMtVtHardGrowthLimit = 3.0f;

float leakageOf(Resizer &resizer, sta::LibertyCell *cell) {
  return resizer.cellLeakage(cell).value_or(std::numeric_limits<float>::max());
}

float readEnvFloat(const char* name, const float default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  char* end = nullptr;
  const float parsed = std::strtof(value, &end);
  return end != value && *end == '\0' ? parsed : default_value;
}

bool readEnvFlag(const char* name, const bool default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) != "0";
}

} // namespace

VtSwapMtGenerator::VtSwapMtGenerator(const GeneratorContext &context)
    : MoveGenerator(context) {}

std::vector<std::unique_ptr<MoveCandidate>>
VtSwapMtGenerator::generate(const Target &target) {
  std::vector<std::unique_ptr<MoveCandidate>> candidates;
  if (!target.isPrepared(kArcDelayStateCache)) {
    return candidates;
  }

  const ArcDelayState &arc_delay = target.arc_delay.value();
  sta::LibertyCell *current_cell = arc_delay.target().arc.currentCell();
  const std::vector<sta::LibertyCell *> candidate_cells =
      selectCandidateCells(current_cell, target);
  candidates.reserve(candidate_cells.size());
  for (sta::LibertyCell *candidate_cell : candidate_cells) {
    candidates.push_back(std::make_unique<VtSwapMtCandidate>(
        resizer_, target, target.driver_pin, target.inst(resizer_),
        current_cell, candidate_cell, arc_delay,
        policy_config_.timing_power_aware_scoring,
        policy_config_.timing_vt_power_penalty));
  }
  return candidates;
}

bool VtSwapMtGenerator::isApplicable(const Target &target) const {
  // Screen out targets that cannot legally change VT in the current library
  // set.
  if (!MoveGenerator::isApplicable(target) ||
      !target.isPrepared(kArcDelayStateCache)) {
    return false;
  }

  sta::Instance *inst = target.inst(resizer_);
  sta::LibertyCell *current_cell = resizer_.network()->libertyCell(inst);
  if (inst == nullptr || current_cell == nullptr || resizer_.dontTouch(inst) ||
      !resizer_.isLogicStdCell(inst) ||
      resizer_.drivesSequentialClockPin(inst) ||
      resizer_.vtCategoryCount() < 2) {
    return false;
  }

  if (resizer_.dbNetwork()->staToDb(current_cell) == nullptr ||
      resizer_.getVTEquivCells(current_cell).size() <= 1) {
    return false;
  }
  return true;
}

std::vector<sta::LibertyCell *>
VtSwapMtGenerator::selectCandidateCells(sta::LibertyCell *current_cell,
                                        const Target &target) const {
  // Enumerate VT-equivalent cells except the current implementation.  The
  // equiv list is sorted by ascending area/leakage, so keep low-leakage
  // recovery candidates broadly eligible and reserve high-leakage speedups for
  // genuinely critical endpoints.
  sta::LibertyCellSeq equiv_cells = resizer_.getVTEquivCells(current_cell);
  const float current_leakage = leakageOf(resizer_, current_cell);
  std::vector<sta::LibertyCell *> candidates;
  candidates.reserve(equiv_cells.size());
  for (sta::LibertyCell *cell : equiv_cells) {
    if (cell == nullptr || cell == current_cell) {
      continue;
    }
    if (!policy_config_.timing_power_aware_scoring) {
      candidates.push_back(cell);
      continue;
    }
    const float cell_leakage = leakageOf(resizer_, cell);
    const float growth_ratio = cell_leakage / std::max(current_leakage, 1.0e-30f);
    const bool leakage_recovery = cell_leakage <= current_leakage;
    const float moderate_slack
        = readEnvFloat("RSZ_MT_VT_MODERATE_SPEEDUP_SLACK",
                       policy_config_.timing_vt_growth_slack);
    const float route_critical_slack
        = readEnvFloat("RSZ_MT_VT_SPEEDUP_SLACK", -2.2e-10f);
    const float hard_slack
        = readEnvFloat("RSZ_MT_VT_HARD_SPEEDUP_SLACK", -3.2e-10f);
    const float moderate_growth_limit
        = readEnvFloat("RSZ_MT_VT_MODERATE_GROWTH_LIMIT",
                       kMtVtModerateGrowthLimit);
    const float hard_growth_limit
        = readEnvFloat("RSZ_MT_VT_HARD_GROWTH_LIMIT", kMtVtHardGrowthLimit);
    const bool allow_broad_speedup =
        readEnvFlag("RSZ_MT_VT_ALLOW_BROAD_SPEEDUP", false);

    if (leakage_recovery ||
        (allow_broad_speedup
         && target.slack <= policy_config_.timing_vt_growth_slack) ||
        (target.slack <= moderate_slack
         && growth_ratio <= moderate_growth_limit) ||
        (target.slack <= route_critical_slack
         && growth_ratio <= moderate_growth_limit) ||
        (target.slack <= hard_slack
         && growth_ratio <= hard_growth_limit)) {
      candidates.push_back(cell);
    }
  }

  if (policy_config_.max_candidate_generation <= 0 ||
      candidates.size() <=
          static_cast<size_t>(policy_config_.max_candidate_generation)) {
    return candidates;
  }

  // Keep the leakage-ascending order returned by getVTEquivCells() and trim
  // only the suffix.
  candidates.resize(policy_config_.max_candidate_generation);
  return candidates;
}

} // namespace rsz
