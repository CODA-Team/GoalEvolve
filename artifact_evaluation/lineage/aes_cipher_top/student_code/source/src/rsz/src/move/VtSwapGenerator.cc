// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "VtSwapGenerator.hh"

#include <limits>
#include <memory>
#include <unordered_set>
#include <vector>

#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "VtSwapCandidate.hh"
#include "db_sta/dbNetwork.hh"
#include "rsz/Resizer.hh"
#include "sta/LibertyClass.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"

namespace rsz {

namespace {

constexpr float kInstanceVtPowerSlackReserve = 2.8e-11f;
constexpr float kModerateVtSpeedupSlack = -1.6e-10f;
constexpr float kHardVtSpeedupSlack = -3.5e-10f;

float leakageOf(Resizer &resizer, sta::LibertyCell *cell) {
  return resizer.cellLeakage(cell).value_or(std::numeric_limits<float>::max());
}

bool modestLeakageGrowth(const float current_leakage,
                         const float candidate_leakage,
                         const float limit)
{
  return candidate_leakage <= current_leakage * limit;
}

} // namespace

VtSwapGenerator::VtSwapGenerator(
    const GeneratorContext &context,
    std::unordered_set<sta::Instance *> *not_swappable)
    : MoveGenerator(context), not_swappable_(not_swappable) {}

bool VtSwapGenerator::isApplicable(const Target &target) const {
  // Base checks both path-driver and instance views via requiredViews().
  // Instance-only targets additionally need the not_swappable_ tracker.
  return MoveGenerator::isApplicable(target) &&
         (target.canBePathDriver() || not_swappable_ != nullptr);
}

std::vector<std::unique_ptr<MoveCandidate>>
VtSwapGenerator::generate(const Target &target) {
  std::vector<std::unique_ptr<MoveCandidate>> candidates;
  sta::Pin *drvr_pin = nullptr;
  sta::Instance *inst = nullptr;
  sta::LibertyCell *curr_cell = nullptr;
  sta::LibertyCell *best_cell = nullptr;
  if (!selectBestCell(target, drvr_pin, inst, curr_cell, best_cell)) {
    return candidates;
  }

  candidates.push_back(std::make_unique<VtSwapCandidate>(
      resizer_, target, drvr_pin, inst, curr_cell, best_cell));
  return candidates;
}

bool VtSwapGenerator::selectBestCell(const Target &target, sta::Pin *&drvr_pin,
                                     sta::Instance *&inst,
                                     sta::LibertyCell *&curr_cell,
                                     sta::LibertyCell *&best_cell) const {
  if (target.canBePathDriver()) {
    return selectPathBestCell(target, drvr_pin, inst, curr_cell, best_cell);
  }
  return selectInstanceBestCell(target, inst, curr_cell, best_cell);
}

bool VtSwapGenerator::selectPathBestCell(const Target &target,
                                         sta::Pin *&drvr_pin,
                                         sta::Instance *&inst,
                                         sta::LibertyCell *&curr_cell,
                                         sta::LibertyCell *&best_cell) const {
  drvr_pin = target.resolvedPin(resizer_);
  if (drvr_pin == nullptr) {
    return false;
  }

  inst = target.inst(resizer_);

  return inst != nullptr && resizer_.vtCategoryCount() >= 2 &&
         !resizer_.dontTouch(inst) && resizer_.isLogicStdCell(inst) &&
         !resizer_.drivesSequentialClockPin(inst) &&
         resolvePathCurrentCell(drvr_pin, curr_cell) &&
         selectBestEquivCell(target, curr_cell, best_cell);
}

bool VtSwapGenerator::selectInstanceBestCell(
    const Target &target, sta::Instance *&inst, sta::LibertyCell *&curr_cell,
    sta::LibertyCell *&best_cell) const {
  inst = target.inst(resizer_);
  if (inst == nullptr) {
    return false;
  }
  curr_cell = resizer_.network()->libertyCell(inst);
  if (curr_cell == nullptr ||
      curr_cell->hasSequentials() ||
      curr_cell->isClockCell() ||
      curr_cell->isClockGate() ||
      !resizer_.checkAndMarkVTSwappable(inst, *not_swappable_, best_cell) ||
      best_cell == nullptr) {
    return false;
  }
  if (target.slack >= kInstanceVtPowerSlackReserve) {
    return selectPowerEquivCell(curr_cell, best_cell);
  }
  if (policy_config_.timing_power_aware_scoring &&
      target.slack >= policy_config_.timing_vt_growth_slack &&
      leakageOf(resizer_, best_cell) > leakageOf(resizer_, curr_cell)) {
    return false;
  }
  return true;
}

bool VtSwapGenerator::resolvePathCurrentCell(
    sta::Pin *drvr_pin, sta::LibertyCell *&curr_cell) const {
  sta::LibertyPort *drvr_port = resizer_.network()->libertyPort(drvr_pin);
  curr_cell = drvr_port != nullptr ? drvr_port->libertyCell() : nullptr;
  return curr_cell != nullptr &&
         !curr_cell->hasSequentials() &&
         !curr_cell->isClockCell() &&
         !curr_cell->isClockGate() &&
         resizer_.dbNetwork()->staToDb(curr_cell) != nullptr;
}

bool VtSwapGenerator::selectBestEquivCell(const Target &target,
                                          sta::LibertyCell *curr_cell,
                                          sta::LibertyCell *&best_cell) const {
  sta::LibertyCellSeq equiv_cells = resizer_.getVTEquivCells(curr_cell);
  best_cell = nullptr;
  if (equiv_cells.empty()) {
    return false;
  }

  const float curr_leakage = leakageOf(resizer_, curr_cell);
  sta::LibertyCell* lowest_leakage_speedup = nullptr;
  sta::LibertyCell* moderate_speedup = nullptr;
  sta::LibertyCell* hard_speedup = nullptr;
  for (sta::LibertyCell *cell : equiv_cells) {
    if (cell == nullptr || cell == curr_cell || resizer_.dontUse(cell)
        || cell->hasSequentials() || cell->isClockCell()
        || cell->isClockGate()) {
      continue;
    }
    const float cell_leakage = leakageOf(resizer_, cell);
    if (cell_leakage <= curr_leakage) {
      best_cell = cell;
      break;
    }
    if (lowest_leakage_speedup == nullptr
        || cell_leakage < leakageOf(resizer_, lowest_leakage_speedup)) {
      lowest_leakage_speedup = cell;
    }
    if (moderate_speedup == nullptr
        && target.slack <= kModerateVtSpeedupSlack
        && modestLeakageGrowth(curr_leakage, cell_leakage, 2.5f)) {
      moderate_speedup = cell;
    }
    if (hard_speedup == nullptr
        && target.slack <= kHardVtSpeedupSlack
        && modestLeakageGrowth(curr_leakage, cell_leakage, 6.0f)) {
      hard_speedup = cell;
    }
  }

  if (best_cell == nullptr
      && (!policy_config_.timing_power_aware_scoring
          || target.slack < policy_config_.timing_vt_growth_slack)) {
    best_cell = equiv_cells.back();
    if (best_cell == curr_cell || best_cell == nullptr
        || resizer_.dontUse(best_cell) || best_cell->hasSequentials()
        || best_cell->isClockCell() || best_cell->isClockGate()) {
      best_cell = lowest_leakage_speedup;
    }
  }

  if (best_cell == nullptr) {
    best_cell = hard_speedup != nullptr ? hard_speedup : moderate_speedup;
  }

  return best_cell != nullptr;
}

bool VtSwapGenerator::selectPowerEquivCell(sta::LibertyCell *curr_cell,
                                           sta::LibertyCell *&best_cell) const {
  sta::LibertyCellSeq equiv_cells = resizer_.getVTEquivCells(curr_cell);
  const float curr_leakage = leakageOf(resizer_, curr_cell);
  float best_leakage = curr_leakage;
  best_cell = nullptr;
  for (sta::LibertyCell *cell : equiv_cells) {
    if (cell == nullptr || cell == curr_cell || resizer_.dontUse(cell)
        || cell->hasSequentials() || cell->isClockCell()
        || cell->isClockGate()) {
      continue;
    }
    const float leakage = leakageOf(resizer_, cell);
    if (leakage < best_leakage) {
      best_leakage = leakage;
      best_cell = cell;
    }
  }
  return best_cell != nullptr;
}

} // namespace rsz
