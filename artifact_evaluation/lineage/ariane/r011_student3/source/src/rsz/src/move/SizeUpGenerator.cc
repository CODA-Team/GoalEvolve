// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SizeUpGenerator.hh"

#include <algorithm>
#include <cstdlib>
#include <cmath>
#include <initializer_list>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "SizeUpCandidate.hh"
#include "db_sta/dbSta.hh"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/GraphDelayCalc.hh"
#include "sta/Liberty.hh"
#include "sta/LibertyClass.hh"
#include "sta/MinMax.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/Path.hh"
#include "sta/TimingArc.hh"

namespace rsz {

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

float growthRatio(const float before, const float after)
{
  return std::max(0.0f, after - before) / positiveOrOne(before);
}

float reductionRatio(const float before, const float after)
{
  return std::max(0.0f, before - after) / positiveOrOne(before);
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

int readSizeUpEnvInt(const char* name, const int default_value)
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

bool readSizeUpEnvFlag(const char* name, const bool default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) != "0";
}

bool hasToken(const std::string& name, const char* lower, const char* upper)
{
  return name.find(lower) != std::string::npos
         || name.find(upper) != std::string::npos;
}

bool lowPowerTimingSizeup()
{
  return readSizeUpEnvFlag("RSZ_SIZEUP_LOW_POWER_TIMING", false);
}

bool hasAnyToken(const std::string& name,
                 const std::initializer_list<const char*> tokens)
{
  return std::any_of(tokens.begin(), tokens.end(), [&name](const char* token) {
    return name.find(token) != std::string::npos;
  });
}

bool innovusLikeCriticalSizing()
{
  return readSizeUpEnvFlag("RSZ_SIZEUP_INNOVUS_LIKE_CRITICAL", false);
}

bool hasDriveToken(const std::string& name,
                   const std::initializer_list<const char*> tokens)
{
  return hasAnyToken(name, tokens);
}

int vtRank(const std::string& name)
{
  if (hasToken(name, "_SL", "_SL")) {
    return 3;
  }
  if (hasToken(name, "_L", "_L")) {
    return 2;
  }
  if (hasToken(name, "_R", "_R")) {
    return 1;
  }
  return 0;
}

float oversizePenalty(sta::LibertyCell* replacement, const Target& target)
{
  if (!lowPowerTimingSizeup() || replacement == nullptr) {
    return 0.0f;
  }
  const std::string name = replacement->name();
  const bool very_critical
      = target.slack <= readSizeUpEnvFloat("RSZ_SIZEUP_OVERSIZE_SLACK",
                                           -3.8e-10f);
  if (very_critical) {
    return 0.0f;
  }
  if (hasAnyToken(name, {"x24", "x16", "x12", "x10", "x8", "x6"})) {
    return readSizeUpEnvFloat("RSZ_SIZEUP_OVERSIZE_PENALTY", 5.0e-12f);
  }
  if (hasAnyToken(name, {"x4", "x5"})) {
    return readSizeUpEnvFloat("RSZ_SIZEUP_LARGE_PENALTY", 1.8e-12f);
  }
  return 0.0f;
}

float mediumDriveBonus(sta::LibertyCell* current_cell,
                       sta::LibertyCell* replacement)
{
  if (!lowPowerTimingSizeup() || current_cell == nullptr
      || replacement == nullptr) {
    return 0.0f;
  }
  const std::string current_name = current_cell->name();
  const std::string replacement_name = replacement->name();
  float bonus = 0.0f;
  if (hasToken(current_name, "xp33", "XP33")
      && hasAnyToken(replacement_name, {"xp5", "XP5", "xp67", "XP67"})) {
    bonus += readSizeUpEnvFloat("RSZ_SIZEUP_XP33_TO_MEDIUM_BONUS", 5.0e-12f);
  }
  if (hasToken(current_name, "_R", "_R")
      && hasAnyToken(replacement_name, {"_L", "_SL"})) {
    bonus += readSizeUpEnvFloat("RSZ_SIZEUP_RVT_SPEED_BONUS", 2.0e-12f);
  }
  return bonus;
}

float weakDriverSizeupBonus(const Target& target,
                            sta::LibertyCell* current_cell,
                            sta::LibertyCell* replacement)
{
  if (!readSizeUpEnvFlag("RSZ_SIZEUP_WEAK_DRIVER_SCORE", true)
      || current_cell == nullptr || replacement == nullptr) {
    return 0.0f;
  }
  const float slack_gate
      = readSizeUpEnvFloat("RSZ_SIZEUP_WEAK_DRIVER_SLACK", -1.5e-10f);
  if (target.slack > slack_gate) {
    return 0.0f;
  }

  const std::string current_name = current_cell->name();
  const std::string replacement_name = replacement->name();
  float bonus = 0.0f;
  if (hasToken(current_name, "xp33", "XP33")
      && !hasToken(replacement_name, "xp33", "XP33")) {
    bonus += readSizeUpEnvFloat("RSZ_SIZEUP_XP33_ESCAPE_BONUS", 9.0e-12f);
  } else if (hasToken(current_name, "xp5", "XP5")
             && !hasToken(replacement_name, "xp5", "XP5")
             && !hasToken(replacement_name, "xp33", "XP33")) {
    bonus += readSizeUpEnvFloat("RSZ_SIZEUP_XP5_ESCAPE_BONUS", 4.0e-12f);
  }
  const float very_critical_slack
      = readSizeUpEnvFloat("RSZ_SIZEUP_WEAK_DRIVER_STRONG_SLACK", -3.5e-10f);
  if (bonus > 0.0f && target.slack <= very_critical_slack) {
    bonus *= readSizeUpEnvFloat("RSZ_SIZEUP_WEAK_DRIVER_STRONG_MULT", 1.5f);
  }
  return bonus;
}

float innovusLikeCriticalBonus(const Target& target,
                               sta::LibertyCell* current_cell,
                               sta::LibertyCell* replacement)
{
  if (!innovusLikeCriticalSizing() || current_cell == nullptr
      || replacement == nullptr) {
    return 0.0f;
  }
  const float slack_gate = readSizeUpEnvFloat(
      "RSZ_SIZEUP_INNOVUS_LIKE_SLACK", -1.5e-10f);
  if (target.slack > slack_gate) {
    return 0.0f;
  }

  const std::string current_name = current_cell->name();
  const std::string replacement_name = replacement->name();
  float bonus = 0.0f;

  if (hasDriveToken(current_name, {"xp33", "XP33", "x1_", "X1_"})
      && hasDriveToken(replacement_name,
                       {"xp5", "XP5", "xp67", "XP67", "x1p5", "X1P5"})) {
    bonus += readSizeUpEnvFloat("RSZ_SIZEUP_INNOVUS_LIKE_MEDIUM_BONUS",
                                9.0e-12f);
  }

  if (vtRank(replacement_name) > vtRank(current_name)
      && (hasToken(replacement_name, "_L", "_L")
          || hasToken(replacement_name, "_SL", "_SL"))) {
    bonus += readSizeUpEnvFloat("RSZ_SIZEUP_INNOVUS_LIKE_VT_BONUS",
                                6.0e-12f);
  }

  if (hasDriveToken(replacement_name,
                    {"x8", "X8", "x10", "X10", "x12", "X12", "x16", "X16",
                     "x24", "X24"})) {
    bonus -= readSizeUpEnvFloat("RSZ_SIZEUP_INNOVUS_LIKE_OVERSIZE_PENALTY",
                                1.2e-11f);
  }
  return bonus;
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

SizeUpGenerator::SizeUpGenerator(const GeneratorContext& context)
    : MoveGenerator(context)
{
}

std::vector<std::unique_ptr<MoveCandidate>> SizeUpGenerator::generate(
    const Target& target)
{
  std::vector<std::unique_ptr<MoveCandidate>> candidates;
  // Build one conventional size-up candidate from the active timing stage.
  sta::Pin* drvr_pin = nullptr;
  sta::Instance* inst = nullptr;
  sta::LibertyPort* drvr_port = nullptr;
  if (!resolveDriverContext(target, drvr_pin, inst, drvr_port)) {
    return candidates;
  }

  const sta::Scene* scene = nullptr;
  const sta::MinMax* min_max = nullptr;
  float load_cap = 0.0f;
  float prev_drive = 0.0f;
  sta::LibertyPort* in_port = nullptr;
  if (!loadStageContext(
          target, drvr_pin, scene, min_max, load_cap, in_port, prev_drive)) {
    return candidates;
  }

  const int max_candidates =
      std::max(1, readSizeUpEnvInt("RSZ_SIZEUP_MULTI_CANDIDATES", 1));
  if (max_candidates <= 1) {
    sta::LibertyCell* replacement = selectReplacement(
        target, in_port, drvr_port, load_cap, prev_drive, scene, min_max);
    if (replacement == nullptr
        || !resizer_.replacementPreservesMaxCap(inst, replacement)) {
      return candidates;
    }

    candidates.push_back(std::make_unique<SizeUpCandidate>(
        resizer_, target, drvr_pin, inst, replacement));
    return candidates;
  }

  for (sta::LibertyCell* replacement :
       selectReplacements(target,
                          in_port,
                          drvr_port,
                          load_cap,
                          prev_drive,
                          scene,
                          min_max,
                          max_candidates)) {
    if (replacement == nullptr
        || !resizer_.replacementPreservesMaxCap(inst, replacement)) {
      continue;
    }
    candidates.push_back(std::make_unique<SizeUpCandidate>(
        resizer_, target, drvr_pin, inst, replacement));
  }
  return candidates;
}

bool SizeUpGenerator::resolveDriverContext(const Target& target,
                                           sta::Pin*& drvr_pin,
                                           sta::Instance*& inst,
                                           sta::LibertyPort*& drvr_port) const
{
  drvr_pin = target.driver_pin;
  if (drvr_pin == nullptr) {
    drvr_pin = target.endpoint_path->pin(resizer_.staState());
  }
  if (drvr_pin == nullptr) {
    return false;
  }

  inst = resizer_.network()->instance(drvr_pin);
  if (inst == nullptr || resizer_.dontTouch(inst)
      || !resizer_.isLogicStdCell(inst)
      || resizer_.drivesSequentialClockPin(inst)) {
    return false;
  }

  drvr_port = resizer_.network()->libertyPort(drvr_pin);
  sta::LibertyCell* current_cell = drvr_port != nullptr
                                       ? drvr_port->libertyCell()
                                       : nullptr;
  return drvr_port != nullptr && current_cell != nullptr
         && !current_cell->hasSequentials()
         && !current_cell->isClockCell()
         && !current_cell->isClockGate();
}

bool SizeUpGenerator::loadStageContext(const Target& target,
                                       sta::Pin* drvr_pin,
                                       const sta::Scene*& scene,
                                       const sta::MinMax*& min_max,
                                       float& load_cap,
                                       sta::LibertyPort*& in_port,
                                       float& prev_drive) const
{
  // Use the current path stage and the upstream driver to estimate delay gain.
  scene = target.activeScene(resizer_);
  min_max = target.minMax(resizer_);
  load_cap
      = resizer_.sta()->graphDelayCalc()->loadCap(drvr_pin, scene, min_max);

  const sta::Path* prev_drvr_path = target.prevDriverPath(resizer_);
  const sta::Path* drvr_path = target.driverPath(resizer_);
  const sta::TimingArc* in_arc = drvr_path != nullptr
                                     ? drvr_path->prevArc(resizer_.staState())
                                     : nullptr;
  in_port = in_arc != nullptr ? in_arc->from() : nullptr;
  if (in_port == nullptr) {
    return false;
  }

  prev_drive = 0.0f;
  if (prev_drvr_path != nullptr) {
    sta::Pin* prev_drvr_pin = prev_drvr_path->pin(resizer_.staState());
    sta::LibertyPort* prev_drvr_port
        = prev_drvr_pin != nullptr
              ? resizer_.network()->libertyPort(prev_drvr_pin)
              : nullptr;
    if (prev_drvr_port != nullptr) {
      prev_drive = prev_drvr_port->driveResistance();
    }
  }
  return true;
}

sta::LibertyCell* SizeUpGenerator::selectReplacement(
    const Target& target,
    sta::LibertyPort* in_port,
    sta::LibertyPort* drvr_port,
    const float load_cap,
    const float prev_drive,
    const sta::Scene* scene,
    const sta::MinMax* min_max) const
{
  return upsizeCell(
      target, in_port, drvr_port, load_cap, prev_drive, scene, min_max);
}

std::vector<sta::LibertyCell*> SizeUpGenerator::selectReplacements(
    const Target& target,
    sta::LibertyPort* in_port,
    sta::LibertyPort* drvr_port,
    const float load_cap,
    const float prev_drive,
    const sta::Scene* scene,
    const sta::MinMax* min_max,
    const int max_candidates) const
{
  return upsizeCells(target,
                     in_port,
                     drvr_port,
                     load_cap,
                     prev_drive,
                     scene,
                     min_max,
                     max_candidates);
}

sta::LibertyCell* SizeUpGenerator::upsizeCell(const Target& target,
                                              sta::LibertyPort* in_port,
                                              sta::LibertyPort* drvr_port,
                                              const float load_cap,
                                              const float prev_drive,
                                              const sta::Scene* scene,
                                              const sta::MinMax* min_max) const
{
  if (scene == nullptr || min_max == nullptr || in_port == nullptr
      || drvr_port == nullptr) {
    return nullptr;
  }

  const int lib_ap = scene->libertyIndex(min_max);
  sta::LibertyCell* cell = drvr_port->libertyCell();
  sta::LibertyCellSeq swappable_cells = resizer_.getSwappableCells(cell);
  if (swappable_cells.empty()) {
    return nullptr;
  }

  // Evaluate stronger equivalent cells first so the search stops on the best
  // local replacement.
  const std::string& in_port_name = in_port->name();
  const std::string& drvr_port_name = drvr_port->name();
  std::ranges::sort(
      swappable_cells.begin(),
      swappable_cells.end(),
      [this, &drvr_port_name, lib_ap](const sta::LibertyCell* cell1,
                                      const sta::LibertyCell* cell2) {
        return strongerCellLess(cell1, cell2, drvr_port_name, lib_ap);
      });

  const sta::LibertyPort* scene_drvr_port
      = static_cast<const sta::LibertyPort*>(drvr_port)->scenePort(lib_ap);
  const sta::LibertyPort* scene_input_port
      = static_cast<const sta::LibertyPort*>(in_port)->scenePort(lib_ap);
  if (scene_drvr_port == nullptr || scene_input_port == nullptr) {
    return nullptr;
  }

  const float drive = scene_drvr_port->driveResistance();
  const float delay = resizer_.gateDelay(drvr_port, load_cap, scene, min_max)
                      + prev_drive * scene_input_port->capacitance();
  const float current_leakage = leakageOf(resizer_, cell);
  const float current_area = cell->area();
  odb::dbMaster* current_master = resizer_.dbNetwork()->staToDb(cell);

  sta::LibertyCell* best_cell = nullptr;
  float best_score = 0.0f;
  for (sta::LibertyCell* swappable : swappable_cells) {
    odb::dbMaster* swappable_master = resizer_.dbNetwork()->staToDb(swappable);
    if (current_master == nullptr || swappable_master == nullptr) {
      continue;
    }
    const bool same_vt = resizer_.cellVTType(current_master).vt_index
                         == resizer_.cellVTType(swappable_master).vt_index;

    sta::LibertyCell* swappable_corner = swappable->sceneCell(lib_ap);
    if (swappable_corner == nullptr) {
      continue;
    }
    sta::LibertyPort* swappable_drvr
        = swappable_corner->findLibertyPort(drvr_port_name);
    sta::LibertyPort* swappable_input
        = swappable_corner->findLibertyPort(in_port_name);
    if (swappable_drvr == nullptr || swappable_input == nullptr) {
      continue;
    }
    const float swappable_drive = swappable_drvr->driveResistance();
    const float swappable_delay
        = resizer_.gateDelay(swappable_drvr, load_cap, scene, min_max)
          + prev_drive * swappable_input->capacitance();
    if (swappable_drive < drive && swappable_delay < delay) {
      const float swappable_leakage = leakageOf(resizer_, swappable);
      if (!same_vt) {
        const bool critical_cross_vt = target.slack <= crossVtCriticalSlack();
        if ((!critical_cross_vt && !policy_config_.timing_power_aware_scoring)
            || !leakageOkForCrossVtSizeUp(
                target, current_leakage, swappable_leakage)) {
          continue;
        }
      }
      const float delay_gain = delay - swappable_delay;
      float score = delay_gain;
      if (policy_config_.timing_power_aware_scoring) {
        const float leakage_ratio =
            growthRatio(current_leakage, swappable_leakage);
        const float leakage_reduction =
            reductionRatio(current_leakage, swappable_leakage);
        const float area_ratio = growthRatio(current_area, swappable->area());
        const float cap_ratio = growthRatio(scene_input_port->capacitance(),
                                            swappable_input->capacitance());
        score -= policy_config_.timing_sizeup_power_penalty
                 * (leakage_ratio + 0.35f * area_ratio);
        score += 0.35f * policy_config_.timing_sizeup_power_penalty
                 * leakage_reduction;
        if (lowPowerTimingSizeup()) {
          const float low_power_cost_weight = readSizeUpEnvFloat(
              "RSZ_SIZEUP_LOW_POWER_COST_WEIGHT", 2.2e-12f);
          score -= low_power_cost_weight
                   * (cap_ratio + 0.45f * area_ratio
                      + 0.35f * leakage_ratio);
        }
        if (readSizeUpEnvFlag("RSZ_SIZEUP_MEDIUM_COST_CANDIDATES", false)) {
          const float medium_cost_weight = readSizeUpEnvFloat(
              "RSZ_SIZEUP_MEDIUM_COST_WEIGHT", 1.5e-12f);
          score -= medium_cost_weight
                   * (cap_ratio + 0.35f * area_ratio
                      + 0.50f * leakage_ratio);
        }
      }
      score += weakDriverSizeupBonus(target, cell, swappable);
      score += mediumDriveBonus(cell, swappable);
      score += innovusLikeCriticalBonus(target, cell, swappable);
      score -= oversizePenalty(swappable, target);
      if (score > best_score) {
        best_score = score;
        best_cell = swappable;
      }
    }
  }

  return best_cell;
}

std::vector<sta::LibertyCell*> SizeUpGenerator::upsizeCells(
    const Target& target,
    sta::LibertyPort* in_port,
    sta::LibertyPort* drvr_port,
    const float load_cap,
    const float prev_drive,
    const sta::Scene* scene,
    const sta::MinMax* min_max,
    const int max_candidates) const
{
  std::vector<sta::LibertyCell*> best_cells;
  if (scene == nullptr || min_max == nullptr || in_port == nullptr
      || drvr_port == nullptr || max_candidates <= 0) {
    return best_cells;
  }

  const int lib_ap = scene->libertyIndex(min_max);
  sta::LibertyCell* cell = drvr_port->libertyCell();
  sta::LibertyCellSeq swappable_cells = resizer_.getSwappableCells(cell);
  if (swappable_cells.empty()) {
    return best_cells;
  }

  const std::string& in_port_name = in_port->name();
  const std::string& drvr_port_name = drvr_port->name();
  std::ranges::sort(
      swappable_cells.begin(),
      swappable_cells.end(),
      [this, &drvr_port_name, lib_ap](const sta::LibertyCell* cell1,
                                      const sta::LibertyCell* cell2) {
        return strongerCellLess(cell1, cell2, drvr_port_name, lib_ap);
      });

  const sta::LibertyPort* scene_drvr_port
      = static_cast<const sta::LibertyPort*>(drvr_port)->scenePort(lib_ap);
  const sta::LibertyPort* scene_input_port
      = static_cast<const sta::LibertyPort*>(in_port)->scenePort(lib_ap);
  if (scene_drvr_port == nullptr || scene_input_port == nullptr) {
    return best_cells;
  }

  const float drive = scene_drvr_port->driveResistance();
  const float delay = resizer_.gateDelay(drvr_port, load_cap, scene, min_max)
                      + prev_drive * scene_input_port->capacitance();
  const float current_leakage = leakageOf(resizer_, cell);
  const float current_area = cell->area();
  odb::dbMaster* current_master = resizer_.dbNetwork()->staToDb(cell);

  std::vector<std::pair<float, sta::LibertyCell*>> scored_cells;
  for (sta::LibertyCell* swappable : swappable_cells) {
    odb::dbMaster* swappable_master = resizer_.dbNetwork()->staToDb(swappable);
    if (current_master == nullptr || swappable_master == nullptr) {
      continue;
    }
    const bool same_vt = resizer_.cellVTType(current_master).vt_index
                         == resizer_.cellVTType(swappable_master).vt_index;

    sta::LibertyCell* swappable_corner = swappable->sceneCell(lib_ap);
    if (swappable_corner == nullptr) {
      continue;
    }
    sta::LibertyPort* swappable_drvr
        = swappable_corner->findLibertyPort(drvr_port_name);
    sta::LibertyPort* swappable_input
        = swappable_corner->findLibertyPort(in_port_name);
    if (swappable_drvr == nullptr || swappable_input == nullptr) {
      continue;
    }
    const float swappable_drive = swappable_drvr->driveResistance();
    const float swappable_delay
        = resizer_.gateDelay(swappable_drvr, load_cap, scene, min_max)
          + prev_drive * swappable_input->capacitance();
    if (!(swappable_drive < drive && swappable_delay < delay)) {
      continue;
    }

    const float swappable_leakage = leakageOf(resizer_, swappable);
    if (!same_vt) {
      const bool critical_cross_vt = target.slack <= crossVtCriticalSlack();
      if ((!critical_cross_vt && !policy_config_.timing_power_aware_scoring)
          || !leakageOkForCrossVtSizeUp(
              target, current_leakage, swappable_leakage)) {
        continue;
      }
    }

    const float delay_gain = delay - swappable_delay;
    float score = delay_gain;
    if (policy_config_.timing_power_aware_scoring) {
      const float leakage_ratio = growthRatio(current_leakage,
                                              swappable_leakage);
      const float leakage_reduction = reductionRatio(current_leakage,
                                                     swappable_leakage);
      const float area_ratio = growthRatio(current_area, swappable->area());
      const float cap_ratio = growthRatio(scene_input_port->capacitance(),
                                          swappable_input->capacitance());
      score -= policy_config_.timing_sizeup_power_penalty
               * (leakage_ratio + 0.35f * area_ratio);
      score += 0.35f * policy_config_.timing_sizeup_power_penalty
               * leakage_reduction;
      if (lowPowerTimingSizeup()) {
        const float low_power_cost_weight = readSizeUpEnvFloat(
            "RSZ_SIZEUP_LOW_POWER_COST_WEIGHT", 2.2e-12f);
        score -= low_power_cost_weight
                 * (cap_ratio + 0.45f * area_ratio
                    + 0.35f * leakage_ratio);
      }
      if (readSizeUpEnvFlag("RSZ_SIZEUP_MEDIUM_COST_CANDIDATES", false)) {
        const float medium_cost_weight = readSizeUpEnvFloat(
            "RSZ_SIZEUP_MEDIUM_COST_WEIGHT", 1.5e-12f);
        score -= medium_cost_weight
                 * (cap_ratio + 0.35f * area_ratio
                    + 0.50f * leakage_ratio);
      }
    }
    score += weakDriverSizeupBonus(target, cell, swappable);
    score += mediumDriveBonus(cell, swappable);
    score += innovusLikeCriticalBonus(target, cell, swappable);
    score -= oversizePenalty(swappable, target);
    if (score > 0.0f) {
      scored_cells.emplace_back(score, swappable);
    }
  }

  std::ranges::sort(scored_cells,
                    [](const auto& lhs, const auto& rhs) {
                      return lhs.first > rhs.first;
                    });
  best_cells.reserve(std::min<int>(max_candidates, scored_cells.size()));
  for (const auto& [score, swappable] : scored_cells) {
    best_cells.push_back(swappable);
    if (best_cells.size() >= static_cast<size_t>(max_candidates)) {
      break;
    }
  }
  return best_cells;
}

}  // namespace rsz
