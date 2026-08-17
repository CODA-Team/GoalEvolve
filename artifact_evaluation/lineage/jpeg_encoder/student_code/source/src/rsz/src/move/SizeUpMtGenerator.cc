// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SizeUpMtGenerator.hh"

#include <algorithm>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "SizeUpMtCandidate.hh"
#include "db_sta/dbNetwork.hh"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/LibertyClass.hh"
#include "sta/MinMax.hh"
#include "sta/NetworkClass.hh"

namespace rsz {

namespace {

float positiveOrOne(const float value)
{
  return value > 0.0f ? value : 1.0f;
}

float leakageOf(Resizer& resizer, sta::LibertyCell* cell)
{
  if (cell == nullptr) {
    return 0.0f;
  }
  return resizer.cellLeakage(cell).value_or(0.0f);
}

float readSizeUpEnvFloat(const char* name, const float default_value)
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

float crossVtCriticalSlack()
{
  return readSizeUpEnvFloat("RSZ_SIZEUP_CROSS_VT_SLACK", 1.0f);
}

bool leakageOkForCrossVtSizeUp(const Target& target,
                               const float current_leakage,
                               const float replacement_leakage)
{
  const float leakage_neutral_limit
      = readSizeUpEnvFloat("RSZ_SIZEUP_CROSS_VT_NEUTRAL_LIMIT", 100.0f);
  const float critical_leakage_limit
      = readSizeUpEnvFloat("RSZ_SIZEUP_CROSS_VT_LEAKAGE_LIMIT", 100.0f);
  const float critical_slack = crossVtCriticalSlack();
  if (replacement_leakage <= current_leakage * leakage_neutral_limit) {
    return true;
  }
  return target.slack <= critical_slack
         && replacement_leakage <= current_leakage * critical_leakage_limit;
}

}  // namespace

SizeUpMtGenerator::SizeUpMtGenerator(const GeneratorContext& context)
    : MoveGenerator(context)
{
}

bool SizeUpMtGenerator::isApplicable(const Target& target) const
{
  sta::Instance* inst = target.inst(resizer_);
  sta::LibertyCell* cell = inst != nullptr
                               ? resizer_.network()->libertyCell(inst)
                               : nullptr;
  return MoveGenerator::isApplicable(target) && inst != nullptr
         && !resizer_.dontTouch(inst) && resizer_.isLogicStdCell(inst)
         && cell != nullptr && !cell->hasSequentials()
         && !cell->isClockCell() && !cell->isClockGate()
         && !resizer_.drivesSequentialClockPin(inst)
         && target.isPrepared(kArcDelayStateCache);
}

std::vector<std::unique_ptr<MoveCandidate>> SizeUpMtGenerator::generate(
    const Target& target)
{
  std::vector<std::unique_ptr<MoveCandidate>> candidates;
  if (!isApplicable(target)) {
    return candidates;
  }

  sta::Instance* target_inst = target.inst(resizer_);
  const ArcDelayState& arc_delay = target.arc_delay.value();
  const SelectedArc& target_arc = arc_delay.target().arc;
  const std::vector<sta::LibertyCell*> replacements = findSizeUpOptions(
      target, target_arc.outputPort(), target_arc.scene, target_arc.min_max);
  candidates.reserve(replacements.size());
  for (sta::LibertyCell* replacement : replacements) {
    if (!resizer_.replacementPreservesMaxCap(target_inst, replacement)) {
      continue;
    }

    candidates.push_back(std::make_unique<SizeUpMtCandidate>(resizer_,
                                                             target,
                                                             target.driver_pin,
                                                             target_inst,
                                                             replacement,
                                                             arc_delay,
                                                             policy_config_
                                                                 .timing_power_aware_scoring,
                                                             policy_config_
                                                                 .timing_sizeup_power_penalty));
  }
  return candidates;
}

std::vector<sta::LibertyCell*> SizeUpMtGenerator::findSizeUpOptions(
    const Target& target,
    const sta::LibertyPort* drvr_port,
    const sta::Scene* scene,
    const sta::MinMax* min_max) const
{
  std::vector<sta::LibertyCell*> replacements;
  if (drvr_port == nullptr || scene == nullptr || min_max == nullptr) {
    return replacements;
  }

  const int lib_ap = scene->libertyIndex(min_max);
  sta::LibertyCell* cell = drvr_port->libertyCell();
  sta::LibertyCellSeq swappable_cells = resizer_.getSwappableCells(cell);
  if (swappable_cells.empty()) {
    return replacements;
  }

  // Rank equivalent cells from strongest to weakest so the first legal win is
  // the best gain.
  const std::string& drvr_port_name = drvr_port->name();
  std::ranges::sort(
      swappable_cells.begin(),
      swappable_cells.end(),
      [this, &drvr_port_name, lib_ap](const sta::LibertyCell* cell1,
                                      const sta::LibertyCell* cell2) {
        return strongerCellLess(cell1, cell2, drvr_port_name, lib_ap);
      });

  const sta::LibertyPort* scene_drvr_port = drvr_port->scenePort(lib_ap);
  if (scene_drvr_port == nullptr) {
    return replacements;
  }

  const float drive = scene_drvr_port->driveResistance();
  // Keep every stronger equivalent cell and let candidate::estimate do the
  // exact timing check.
  odb::dbMaster* current_master = resizer_.dbNetwork()->staToDb(cell);
  const float current_leakage = leakageOf(resizer_, cell);
  for (sta::LibertyCell* swappable : swappable_cells) {
    odb::dbMaster* swappable_master = resizer_.dbNetwork()->staToDb(swappable);
    if (current_master == nullptr || swappable_master == nullptr) {
      continue;
    }

    const bool same_vt = resizer_.cellVTType(current_master).vt_index
                         == resizer_.cellVTType(swappable_master).vt_index;
    if (!same_vt) {
      const bool critical_cross_vt = target.slack <= crossVtCriticalSlack();
      if ((!critical_cross_vt && !policy_config_.timing_power_aware_scoring)
          || !leakageOkForCrossVtSizeUp(
              target, current_leakage, leakageOf(resizer_, swappable))) {
        continue;
      }
    }

    sta::LibertyCell* swappable_corner = swappable->sceneCell(lib_ap);
    if (swappable_corner == nullptr) {
      continue;
    }
    sta::LibertyPort* swappable_drvr
        = swappable_corner->findLibertyPort(drvr_port_name);
    if (swappable_drvr == nullptr) {
      continue;
    }
    const float swappable_drive = swappable_drvr->driveResistance();
    if (swappable_drive < drive) {
      replacements.push_back(swappable);
    }
  }
  std::ranges::sort(replacements, [this, cell](sta::LibertyCell* lhs,
                                               sta::LibertyCell* rhs) {
    const float current_leakage = leakageOf(resizer_, cell);
    const float lhs_growth =
        std::max(0.0f, leakageOf(resizer_, lhs) - current_leakage)
        / positiveOrOne(current_leakage);
    const float rhs_growth =
        std::max(0.0f, leakageOf(resizer_, rhs) - current_leakage)
        / positiveOrOne(current_leakage);
    if (lhs_growth != rhs_growth) {
      return lhs_growth < rhs_growth;
    }
    return lhs->area() < rhs->area();
  });
  return replacements;
}

}  // namespace rsz
