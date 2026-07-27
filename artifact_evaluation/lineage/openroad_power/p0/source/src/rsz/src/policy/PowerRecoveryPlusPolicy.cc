// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "PowerRecoveryPlusPolicy.hh"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

#include "MoveCommitter.hh"
#include "PowerRecoveryCandidate.hh"
#include "db_sta/dbNetwork.hh"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/Delay.hh"
#include "sta/Graph.hh"
#include "sta/GraphClass.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/PortDirection.hh"
#include "sta/PowerClass.hh"
#include "sta/Sta.hh"
#include "utl/Logger.h"
#include "utl/env.h"

namespace rsz {

using utl::RSZ;

namespace {

double positiveOrDefault(const double value, const double fallback) {
  return value > 0.0 ? value : fallback;
}

double readDoubleEnv(const char *name, const double fallback) {
  const char *value = std::getenv(name);
  if (value == nullptr || value[0] == '\0') {
    return fallback;
  }
  try {
    return std::stod(value);
  } catch (...) {
    return fallback;
  }
}

bool readBoolEnv(const char *name, const bool fallback) {
  const char *value = std::getenv(name);
  if (value == nullptr || value[0] == '\0') {
    return fallback;
  }
  const std::string text(value);
  return text == "1" || text == "true" || text == "TRUE" || text == "yes" ||
         text == "on";
}

bool hasEnv(const char *name) { return std::getenv(name) != nullptr; }

} // namespace

PowerRecoveryPlusPolicy::PowerRecoveryPlusPolicy(
    Resizer &resizer, MoveCommitter &committer,
    RepairSetupContext &setup_context, const OptimizerRunConfig &config)
    : OptimizationPolicy(resizer, committer, setup_context, config) {
  is_experimental = true;
}

PowerRecoveryPlusPolicy::~PowerRecoveryPlusPolicy() = default;

bool PowerRecoveryPlusPolicy::start() {
  OptimizationPolicy::start();
  loadConfig();
  baseline_ = collectMetrics();
  committed_moves_ = 0;
  trial_count_ = 0;
  rejected_count_ = 0;
  accepted_size_down_ = 0;
  accepted_vt_recover_ = 0;
  accepted_size_down_and_vt_ = 0;
  accepted_remove_buffer_ = 0;
  reject_reason_counts_.clear();
  exhausted_instances_.clear();
  logger_->info(RSZ, 2302,
                "POWER_RECOVERY_PLUS|start|phase={}|proportion={:.3g}|"
                "wns={}|tns={}|leakage_proxy={:.6g}|"
                "area={:.6g}|max_moves={}|max_targets={}|trial_limit={}|"
                "power_weight={:.3g}|tns_weight={:.3g}|wns_weight={:.3g}|"
                "allow_negative_wns={}|"
                "buffer_removal={}|cell_recovery={}|vt_recovery={}|greedy={}",
                power_config_.phase, power_config_.proportion,
                sta::delayAsString(baseline_.wns, 3, sta_),
                sta::delayAsString(baseline_.tns, 3, sta_),
                baseline_.leakage_proxy, baseline_.area,
                power_config_.max_moves, power_config_.max_targets,
                power_config_.trial_limit, power_config_.power_weight,
                power_config_.tns_weight, power_config_.wns_weight,
                power_config_.allow_negative_wns ? "true" : "false",
                power_config_.enable_buffer_removal ? "true" : "false",
                power_config_.enable_cell_recovery ? "true" : "false",
                power_config_.enable_vt_recovery ? "true" : "false",
                power_config_.greedy_commit ? "true" : "false");
  return true;
}

void PowerRecoveryPlusPolicy::loadConfig() {
  power_config_.phase = config_.power_phase.empty()
                            ? std::string("legacy_power_recovery_plus")
                            : config_.power_phase;
  power_config_.proportion = std::clamp(config_.power_proportion, 0.0f, 1.0f);
  power_config_.max_moves =
      utl::readEnvarNonNegativeInt("RSZ_PWR_PLUS_MAX_MOVES", 200);
  power_config_.max_targets =
      utl::readEnvarNonNegativeInt("RSZ_PWR_PLUS_MAX_TARGETS", 3000);
  power_config_.max_candidates_per_inst =
      utl::readEnvarNonNegativeInt("RSZ_PWR_PLUS_CANDIDATES_PER_INST", 4);
  power_config_.trial_limit =
      utl::readEnvarNonNegativeInt("RSZ_PWR_PLUS_TRIAL_LIMIT", 1200);
  power_config_.batch_size =
      std::max(1, utl::readEnvarNonNegativeInt("RSZ_PWR_PLUS_BATCH_SIZE", 12));
  power_config_.power_weight =
      readDoubleEnv("RSZ_PWR_PLUS_POWER_WEIGHT", 100.0);
  power_config_.tns_weight = readDoubleEnv("RSZ_PWR_PLUS_TNS_WEIGHT", 30.0);
  power_config_.wns_weight = readDoubleEnv("RSZ_PWR_PLUS_WNS_WEIGHT", 0.0);
  power_config_.criticality_weight =
      readDoubleEnv("RSZ_PWR_PLUS_CRIT_WEIGHT", 8.0);
  power_config_.area_power_scale =
      readDoubleEnv("RSZ_PWR_PLUS_AREA_POWER_SCALE", 0.0);
  power_config_.input_cap_power_scale =
      readDoubleEnv("RSZ_PWR_PLUS_INPUT_CAP_POWER_SCALE", 1.0e7);
  power_config_.min_power_gain =
      readDoubleEnv("RSZ_PWR_PLUS_MIN_POWER_GAIN", 0.0);
  power_config_.min_power_guard_score =
      readDoubleEnv("RSZ_PWR_PLUS_MIN_POWER_GUARD_SCORE", 0.0);
  power_config_.buffer_removal_bonus =
      readDoubleEnv("RSZ_PWR_PLUS_BUFFER_REMOVAL_BONUS", 0.15);
  power_config_.min_target_slack =
      readDoubleEnv("RSZ_PWR_PLUS_MIN_TARGET_SLACK", 4.5e-11);
  power_config_.min_unbuffer_slack =
      readDoubleEnv("RSZ_PWR_PLUS_MIN_UNBUFFER_SLACK", 4.5e-11);
  power_config_.max_tns_sacrifice_ratio =
      readDoubleEnv("RSZ_PWR_PLUS_MAX_TNS_SACRIFICE_RATIO", 0.018);
  power_config_.max_wns_sacrifice =
      readDoubleEnv("RSZ_PWR_PLUS_MAX_WNS_SACRIFICE", 2.0e-12);
  power_config_.max_unbuffer_fanout =
      utl::readEnvarNonNegativeInt("RSZ_PWR_PLUS_MAX_UNBUFFER_FANOUT", 4);
  power_config_.max_unbuffer_result_fanout = utl::readEnvarNonNegativeInt(
      "RSZ_PWR_PLUS_MAX_UNBUFFER_RESULT_FANOUT", 4);
  power_config_.match_cell_footprint =
      readBoolEnv("RSZ_PWR_PLUS_MATCH_FOOTPRINT", true);
  power_config_.allow_negative_wns =
      readBoolEnv("RSZ_PWR_PLUS_ALLOW_NEGATIVE_WNS", true);
  power_config_.enable_buffer_removal =
      readBoolEnv("RSZ_PWR_PLUS_ENABLE_BUFFER_REMOVAL", true);
  power_config_.enable_cell_recovery =
      readBoolEnv("RSZ_PWR_PLUS_ENABLE_CELL_RECOVERY", true);
  power_config_.enable_vt_recovery =
      readBoolEnv("RSZ_PWR_PLUS_ENABLE_VT_RECOVERY", true);
  power_config_.greedy_commit =
      readBoolEnv("RSZ_PWR_PLUS_GREEDY_COMMIT", false);
  power_config_.skip_sequential =
      readBoolEnv("RSZ_PWR_PLUS_SKIP_SEQUENTIAL", true);
  power_config_.require_leakage_gain =
      readBoolEnv("RSZ_PWR_PLUS_REQUIRE_LEAKAGE_GAIN", true);
  power_config_.require_local_power_gain =
      readBoolEnv("RSZ_PWR_PLUS_REQUIRE_LOCAL_POWER_GAIN", false);
  power_config_.verbose = readBoolEnv("RSZ_PWR_PLUS_VERBOSE", false);

  if (power_config_.phase == "early_forced_reclaim") {
    const int eligible_count = std::max(1, eligibleLogicInstanceCount());
    const int proportional_moves =
        std::max(25, static_cast<int>(eligible_count *
                                      power_config_.proportion * 0.12f));
    if (config_.power_max_moves > 0) {
      power_config_.max_moves = config_.power_max_moves;
    } else if (!hasEnv("RSZ_PWR_PLUS_MAX_MOVES")) {
      power_config_.max_moves = std::min(proportional_moves, 12000);
    }
    if (!hasEnv("RSZ_PWR_PLUS_MAX_TARGETS")) {
      power_config_.max_targets =
          std::min(std::min(std::max(1000,
                                      static_cast<int>(eligible_count *
                                                       power_config_.proportion)),
                             eligible_count),
                   12000);
    }
    if (!hasEnv("RSZ_PWR_PLUS_TRIAL_LIMIT")) {
      power_config_.trial_limit =
          std::min(std::max(power_config_.max_moves * 3, 2000), 12000);
    }
    if (!hasEnv("RSZ_PWR_PLUS_BATCH_SIZE")) {
      power_config_.batch_size = std::min(power_config_.max_moves, 512);
    }
    if (!hasEnv("RSZ_PWR_PLUS_MIN_TARGET_SLACK")) {
      power_config_.min_target_slack = -2.5e-11;
    }
    if (!hasEnv("RSZ_PWR_PLUS_MIN_UNBUFFER_SLACK")) {
      power_config_.min_unbuffer_slack = 2.0e-11;
    }
    if (!hasEnv("RSZ_PWR_PLUS_MAX_TNS_SACRIFICE_RATIO")) {
      power_config_.max_tns_sacrifice_ratio =
          config_.power_max_tns_expand_ratio > 0.0
              ? config_.power_max_tns_expand_ratio
              : 8.0;
    }
    if (!hasEnv("RSZ_PWR_PLUS_MAX_WNS_SACRIFICE")) {
      power_config_.max_wns_sacrifice =
          config_.power_max_wns_drop > 0.0 ? config_.power_max_wns_drop
                                           : 5.0e-10;
    }
    if (!hasEnv("RSZ_PWR_PLUS_MIN_POWER_GUARD_SCORE")) {
      power_config_.min_power_guard_score = -1.0e30;
    }
    if (!hasEnv("RSZ_PWR_PLUS_POWER_WEIGHT")) {
      power_config_.power_weight = 260.0;
    }
    if (!hasEnv("RSZ_PWR_PLUS_TNS_WEIGHT")) {
      power_config_.tns_weight = 3.0;
    }
    if (!hasEnv("RSZ_PWR_PLUS_CRIT_WEIGHT")) {
      power_config_.criticality_weight = 2.0;
    }
    if (!hasEnv("RSZ_PWR_PLUS_INPUT_CAP_POWER_SCALE")) {
      power_config_.input_cap_power_scale = 2.0e7;
    }
    if (!hasEnv("RSZ_PWR_PLUS_MAX_UNBUFFER_FANOUT")) {
      power_config_.max_unbuffer_fanout = 3;
    }
    if (!hasEnv("RSZ_PWR_PLUS_MAX_UNBUFFER_RESULT_FANOUT")) {
      power_config_.max_unbuffer_result_fanout = 6;
    }
    power_config_.skip_sequential = true;
    power_config_.allow_negative_wns = true;
    power_config_.enable_buffer_removal = true;
    power_config_.enable_cell_recovery = true;
    power_config_.greedy_commit = false;
    power_config_.require_local_power_gain = false;
  } else if (power_config_.phase == "mid_area_reclaim") {
    if (config_.power_max_moves > 0) {
      power_config_.max_moves = config_.power_max_moves;
    }
  } else if (power_config_.phase == "late_leakage_recovery") {
    if (!hasEnv("RSZ_PWR_PLUS_ENABLE_BUFFER_REMOVAL")) {
      power_config_.enable_buffer_removal = false;
    }
    if (config_.power_max_moves > 0) {
      power_config_.max_moves = config_.power_max_moves;
    }
  }
}

int PowerRecoveryPlusPolicy::eligibleLogicInstanceCount() const {
  int count = 0;
  sta::LeafInstanceIterator *iter = network_->leafInstanceIterator();
  while (iter->hasNext()) {
    sta::Instance *inst = iter->next();
    if (resizer_.dontTouch(inst) || !resizer_.isLogicStdCell(inst) ||
        resizer_.drivesSequentialClockPin(inst)) {
      continue;
    }
    sta::LibertyCell *cell = network_->libertyCell(inst);
    if (cell == nullptr || resizer_.dontUse(cell) || cell->hasSequentials()) {
      continue;
    }
    ++count;
  }
  delete iter;
  return count;
}

PowerRecoveryPlusPolicy::Metrics
PowerRecoveryPlusPolicy::collectMetrics() const {
  Metrics metrics;
  sta::Vertex *worst_vertex = nullptr;
  sta_->worstSlack(max_, metrics.wns, worst_vertex);
  metrics.tns = sta_->totalNegativeSlack(max_);
  metrics.area = resizer_.computeDesignArea();

  sta::LeafInstanceIterator *iter = network_->leafInstanceIterator();
  while (iter->hasNext()) {
    sta::Instance *inst = iter->next();
    if (!resizer_.isLogicStdCell(inst)) {
      continue;
    }
    sta::LibertyCell *cell = network_->libertyCell(inst);
    if (cell != nullptr) {
      metrics.leakage_proxy += cellLeakage(cell);
    }
  }
  delete iter;
  return metrics;
}

void PowerRecoveryPlusPolicy::iterate() {
  if (converged_) {
    return;
  }

  bool changed = false;
  Metrics current = collectMetrics();
  while (committed_moves_ < power_config_.max_moves &&
         trial_count_ < power_config_.trial_limit) {
    std::vector<TargetInfo> targets = collectTargets();
    if (targets.empty()) {
      break;
    }

    std::vector<TrialCandidate> legal_candidates;
    bool greedy_committed = false;
    const bool early_forced =
        power_config_.phase == "early_forced_reclaim";
    const size_t target_candidate_count =
        early_forced
            ? static_cast<size_t>(std::max(
                  1, std::min(power_config_.max_moves - committed_moves_,
                              power_config_.batch_size)))
            : std::numeric_limits<size_t>::max();
    for (const TargetInfo &target : targets) {
      std::vector<TrialCandidate> candidates = generateCandidates(target);
      for (TrialCandidate &candidate : candidates) {
        if (trial_count_ >= power_config_.trial_limit) {
          break;
        }
        ++trial_count_;
        if (measureCandidate(candidate, current) &&
            passesCatastrophicBudget(candidate, baseline_)) {
          candidate.score = scoreCandidate(candidate, current, baseline_);
          if (candidate.score > 0.0) {
            if (power_config_.greedy_commit) {
              if (commitCandidate(candidate)) {
                changed = true;
                ++committed_moves_;
                exhausted_instances_.insert(candidate.target.inst);
                current = collectMetrics();
                greedy_committed = true;
              } else {
                ++rejected_count_;
                recordReject("commit");
                exhausted_instances_.insert(candidate.target.inst);
              }
              break;
            }
            legal_candidates.push_back(candidate);
            if (legal_candidates.size() >= target_candidate_count) {
              break;
            }
          } else {
            ++rejected_count_;
            recordReject("score");
          }
        } else {
          ++rejected_count_;
          recordReject(candidate.reject_reason.empty() ? "budget_or_measure"
                                                       : candidate.reject_reason);
        }
      }
      if (trial_count_ >= power_config_.trial_limit) {
        break;
      }
      if (legal_candidates.size() >= target_candidate_count) {
        break;
      }
      if (greedy_committed) {
        break;
      }
    }

    if (greedy_committed) {
      continue;
    }

    if (legal_candidates.empty()) {
      break;
    }

    std::ranges::sort(legal_candidates,
                      [](const TrialCandidate &lhs, const TrialCandidate &rhs) {
                        return lhs.score > rhs.score;
                      });

    int accepted_this_round = 0;
    for (const TrialCandidate &candidate : legal_candidates) {
      if (accepted_this_round >= power_config_.batch_size ||
          committed_moves_ >= power_config_.max_moves) {
        break;
      }
      if (exhausted_instances_.contains(candidate.target.inst)) {
        continue;
      }
      if (commitCandidate(candidate)) {
        changed = true;
        ++committed_moves_;
        ++accepted_this_round;
        exhausted_instances_.insert(candidate.target.inst);
      } else {
        ++rejected_count_;
        recordReject("commit");
        exhausted_instances_.insert(candidate.target.inst);
      }
    }

    if (accepted_this_round == 0) {
      break;
    }
    current = collectMetrics();
  }

  const Metrics final_metrics = collectMetrics();
  logger_->info(
      RSZ, 2303,
      "POWER_RECOVERY_PLUS|summary|committed={}|trials={}|rejected={}|"
      "accepted_size_down={}|accepted_vt_recover={}|"
      "accepted_size_down_and_vt={}|accepted_remove_buffer={}|"
      "wns_before={}|wns_after={}|tns_before={}|tns_after={}|"
      "leakage_before={:.6g}|leakage_after={:.6g}|"
      "leakage_delta={:.6g}|area_before={:.6g}|area_after={:.6g}|"
      "reject_reasons={}",
      committed_moves_, trial_count_, rejected_count_, accepted_size_down_,
      accepted_vt_recover_, accepted_size_down_and_vt_,
      accepted_remove_buffer_,
      sta::delayAsString(baseline_.wns, 3, sta_),
      sta::delayAsString(final_metrics.wns, 3, sta_),
      sta::delayAsString(baseline_.tns, 3, sta_),
      sta::delayAsString(final_metrics.tns, 3, sta_), baseline_.leakage_proxy,
      final_metrics.leakage_proxy,
      baseline_.leakage_proxy - final_metrics.leakage_proxy, baseline_.area,
      final_metrics.area, rejectReasonSummary());

  markRunComplete(changed || committed_moves_ > 0);
}

std::vector<PowerRecoveryPlusPolicy::TargetInfo>
PowerRecoveryPlusPolicy::collectTargets() {
  std::vector<TargetInfo> targets;
  targets.reserve(std::min(10000, power_config_.max_targets));

  sta::LeafInstanceIterator *iter = network_->leafInstanceIterator();
  while (iter->hasNext()) {
    sta::Instance *inst = iter->next();
    if (exhausted_instances_.contains(inst) || resizer_.dontTouch(inst) ||
        !resizer_.isLogicStdCell(inst) ||
        resizer_.drivesSequentialClockPin(inst)) {
      continue;
    }
    sta::LibertyCell *current_cell = network_->libertyCell(inst);
    if (current_cell == nullptr || resizer_.dontUse(current_cell)) {
      continue;
    }
    // The contest power is dominated by combinational internal/switching power
    // on AES.  Avoid spending the trial budget on flops unless explicitly
    // requested; the previous smoke run mostly found DFF VT swaps with no
    // measurable official power benefit.
    if (power_config_.skip_sequential && current_cell->hasSequentials()) {
      continue;
    }
    const bool is_buffer = current_cell->isBuffer();
    sta::Pin *output_pin = findOutputPin(inst);
    if (output_pin == nullptr || sta_->isClock(output_pin, sta_->cmdMode())) {
      continue;
    }

    const double leakage = cellLeakage(current_cell);
    const double area = cellArea(current_cell);
    const double input_cap = inputCapProxy(current_cell);
    if (leakage <= 0.0 && area <= 0.0 && input_cap <= 0.0) {
      continue;
    }

    TargetInfo target;
    target.inst = inst;
    target.output_pin = output_pin;
    target.current_cell = current_cell;
    target.worst_slack = worstOutputSlack(inst);
    const double slack_floor = is_buffer && power_config_.enable_buffer_removal
                                   ? power_config_.min_unbuffer_slack
                                   : power_config_.min_target_slack;
    if (target.worst_slack < slack_floor) {
      continue;
    }
    target.fanout = outputFanout(output_pin);
    target.leakage = leakage;
    target.area = area;
    target.input_cap = input_cap;
    // Innovus evidence: power recovery mostly happens away from the top timing
    // paths.  Positive slack and high leakage/area/cap get first look, but
    // mildly critical cells remain eligible for score-based sacrifice.
    const double slack_bonus = std::max(0.0f, target.worst_slack);
    const double fanout_penalty = std::max(0, target.fanout - 8) * 0.02;
    target.priority = leakage * 1.0e6 + area * 1.0e-3 + input_cap +
                      slack_bonus * 10.0 - fanout_penalty;
    if (is_buffer && power_config_.enable_buffer_removal &&
        target.fanout <= power_config_.max_unbuffer_fanout &&
        passesBufferRemovalFanoutGuard(target)) {
      // Innovus' post-opt netlists show that power recovery is often topology
      // cleanup, not just weaker-cell replacement.  Give removable buffers an
      // early trial budget because one accepted deletion can remove leakage,
      // internal power and one load pin at once.
      target.priority += 0.5 + leakage * 5.0e6;
    }
    targets.push_back(target);
  }
  delete iter;

  std::ranges::sort(targets, [](const TargetInfo &lhs, const TargetInfo &rhs) {
    return lhs.priority > rhs.priority;
  });
  if (targets.size() > static_cast<size_t>(power_config_.max_targets)) {
    targets.resize(power_config_.max_targets);
  }
  return targets;
}

std::vector<PowerRecoveryPlusPolicy::TrialCandidate>
PowerRecoveryPlusPolicy::generateCandidates(const TargetInfo &target) {
  std::vector<TrialCandidate> candidates;
  std::unordered_set<sta::LibertyCell *> seen;

  if (power_config_.enable_buffer_removal && target.current_cell->isBuffer() &&
      target.fanout <= power_config_.max_unbuffer_fanout &&
      passesBufferRemovalFanoutGuard(target) &&
      resizer_.canRemoveBuffer(target.inst, true)) {
    TrialCandidate candidate;
    candidate.target = target;
    candidate.candidate_cell = nullptr;
    candidate.kind = PowerRecoveryMoveKind::kRemoveBuffer;
    candidate.leakage_gain = target.leakage;
    candidate.area_gain = target.area;
    candidate.input_cap_gain = target.input_cap;
    candidate.power_gain =
        std::max(0.0, candidate.leakage_gain) +
        std::max(0.0, candidate.area_gain) * power_config_.area_power_scale +
        std::max(0.0, candidate.input_cap_gain) *
            power_config_.input_cap_power_scale;
    if (candidate.power_gain > power_config_.min_power_gain) {
      candidates.push_back(candidate);
    }
  }

  auto add_candidate = [&](sta::LibertyCell *candidate_cell) {
    if (candidate_cell == nullptr || candidate_cell == target.current_cell ||
        seen.contains(candidate_cell) || resizer_.dontUse(candidate_cell) ||
        !footprintOk(target.current_cell, candidate_cell)) {
      return;
    }
    const PowerRecoveryMoveKind kind =
        classifyMove(target.current_cell, candidate_cell);
    if (!isUsefulPowerCandidate(target.current_cell, candidate_cell, kind)) {
      return;
    }
    TrialCandidate candidate;
    candidate.target = target;
    candidate.candidate_cell = candidate_cell;
    candidate.kind = kind;
    candidate.leakage_gain =
        cellLeakage(target.current_cell) - cellLeakage(candidate_cell);
    candidate.area_gain =
        cellArea(target.current_cell) - cellArea(candidate_cell);
    candidate.input_cap_gain =
        inputCapProxy(target.current_cell) - inputCapProxy(candidate_cell);
    candidate.power_gain =
        std::max(0.0, candidate.leakage_gain) +
        std::max(0.0, candidate.area_gain) * power_config_.area_power_scale +
        std::max(0.0, candidate.input_cap_gain) *
            power_config_.input_cap_power_scale;
    if (candidate.power_gain <= power_config_.min_power_gain) {
      return;
    }
    seen.insert(candidate_cell);
    candidates.push_back(candidate);
  };

  if (!power_config_.enable_cell_recovery) {
    return candidates;
  }

  auto add_until_cap = [&](const sta::LibertyCellSeq &cells,
                           const bool allow_vt_only) {
    for (sta::LibertyCell *cell : cells) {
      const size_t before_count = candidates.size();
      add_candidate(cell);
      if (!allow_vt_only && candidates.size() > before_count &&
          candidates.back().kind == PowerRecoveryMoveKind::kVtRecover) {
        candidates.pop_back();
      }
      if (candidates.size() >=
          static_cast<size_t>(power_config_.max_candidates_per_inst)) {
        return true;
      }
    }
    return false;
  };

  if (power_config_.phase == "early_forced_reclaim") {
    if (add_until_cap(resizer_.getSwappableCells(target.current_cell),
                      /*allow_vt_only=*/false)) {
      return candidates;
    }
    if (power_config_.enable_vt_recovery) {
      add_until_cap(resizer_.getVTEquivCells(target.current_cell),
                    /*allow_vt_only=*/true);
    }
    return candidates;
  }

  if (power_config_.enable_vt_recovery &&
      add_until_cap(resizer_.getVTEquivCells(target.current_cell),
                    /*allow_vt_only=*/true)) {
    return candidates;
  }
  add_until_cap(resizer_.getSwappableCells(target.current_cell),
                /*allow_vt_only=*/true);
  return candidates;
}

bool PowerRecoveryPlusPolicy::measureCandidate(TrialCandidate &candidate,
                                               const Metrics &before) {
  if (candidate.kind == PowerRecoveryMoveKind::kRemoveBuffer) {
    if (!resizer_.canRemoveBuffer(candidate.target.inst, true)) {
      candidate.reject_reason = "can_remove_buffer";
      return false;
    }
    if (!passesBufferRemovalFanoutGuard(candidate.target)) {
      candidate.reject_reason = "fanout_guard";
      return false;
    }

    const std::string inst_name = instName(candidate.target.inst);
    candidate.wns_before = before.wns;
    candidate.tns_before = before.tns;
    resizer_.journalBegin();
    const bool removed = resizer_.removeBuffer(candidate.target.inst);
    if (!removed) {
      resizer_.journalRestore();
      candidate.target.inst = network_->findInstance(inst_name);
      candidate.target.output_pin = candidate.target.inst != nullptr
                                        ? findOutputPin(candidate.target.inst)
                                        : nullptr;
      candidate.reject_reason = "remove_failed";
      return false;
    }

    resizer_.updateParasiticsAndTiming();
    sta::Vertex *worst_vertex = nullptr;
    sta_->worstSlack(max_, candidate.wns_after, worst_vertex);
    candidate.tns_after = sta_->totalNegativeSlack(max_);

    resizer_.journalRestore();
    candidate.target.inst = network_->findInstance(inst_name);
    candidate.target.output_pin = candidate.target.inst != nullptr
                                      ? findOutputPin(candidate.target.inst)
                                      : nullptr;
    if (candidate.target.inst == nullptr ||
        candidate.target.output_pin == nullptr) {
      candidate.reject_reason = "restore_failed";
      return false;
    }

    candidate.delta_wns = candidate.wns_after - candidate.wns_before;
    candidate.delta_tns = candidate.tns_after - candidate.tns_before;
    if (power_config_.verbose) {
      logger_->report(
          "POWER_RECOVERY_PLUS|candidate|inst={}|kind={}|from={}|to=<deleted>|"
          "power_gain={:.6g}|leak_gain={:.6g}|area_gain={:.6g}|"
          "cap_gain={:.6g}|dwns={}|dtns={}",
          inst_name, powerRecoveryMoveKindName(candidate.kind),
          candidate.target.current_cell->name(), candidate.power_gain,
          candidate.leakage_gain, candidate.area_gain, candidate.input_cap_gain,
          sta::delayAsString(candidate.delta_wns, 3, sta_),
          sta::delayAsString(candidate.delta_tns, 3, sta_));
    }
    return true;
  }

  if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                           candidate.candidate_cell)) {
    candidate.reject_reason = "max_cap";
    return false;
  }

  candidate.wns_before = before.wns;
  candidate.tns_before = before.tns;
  if (power_config_.require_local_power_gain) {
    candidate.local_power_before =
        sta_->power(candidate.target.inst, sta_->cmdScene()).total();
  }

  odb::dbDatabase::beginEco(resizer_.block());
  const bool replaced =
      resizer_.replaceCell(candidate.target.inst, candidate.candidate_cell);
  if (!replaced) {
    resizer_.initForJournalRestore();
    odb::dbDatabase::undoEco(resizer_.block());
    candidate.reject_reason = "replace_failed";
    return false;
  }

  resizer_.updateParasiticsAndTiming();
  if (power_config_.require_local_power_gain) {
    candidate.local_power_after =
        sta_->power(candidate.target.inst, sta_->cmdScene()).total();
  }
  sta::Vertex *worst_vertex = nullptr;
  sta_->worstSlack(max_, candidate.wns_after, worst_vertex);
  candidate.tns_after = sta_->totalNegativeSlack(max_);

  resizer_.initForJournalRestore();
  odb::dbDatabase::undoEco(resizer_.block());
  resizer_.updateParasiticsAndTiming();

  candidate.delta_wns = candidate.wns_after - candidate.wns_before;
  candidate.delta_tns = candidate.tns_after - candidate.tns_before;
  if (power_config_.require_local_power_gain) {
    const double measured_local_power_gain =
        candidate.local_power_before - candidate.local_power_after;
    if (measured_local_power_gain <= 0.0) {
      candidate.reject_reason = "local_power";
      return false;
    }
    candidate.power_gain = measured_local_power_gain;
  }

  if (power_config_.verbose) {
    logger_->report(
        "POWER_RECOVERY_PLUS|candidate|inst={}|kind={}|from={}|to={}|"
        "power_gain={:.6g}|leak_gain={:.6g}|area_gain={:.6g}|"
        "cap_gain={:.6g}|local_power_before={:.6g}|"
        "local_power_after={:.6g}|dwns={}|dtns={}",
        instName(candidate.target.inst),
        powerRecoveryMoveKindName(candidate.kind),
        candidate.target.current_cell->name(), candidate.candidate_cell->name(),
        candidate.power_gain, candidate.leakage_gain, candidate.area_gain,
        candidate.input_cap_gain, candidate.local_power_before,
        candidate.local_power_after,
        sta::delayAsString(candidate.delta_wns, 3, sta_),
        sta::delayAsString(candidate.delta_tns, 3, sta_));
  }
  return true;
}

bool PowerRecoveryPlusPolicy::passesCatastrophicBudget(
    const TrialCandidate &candidate, const Metrics &baseline) const {
  if (!power_config_.allow_negative_wns && candidate.wns_after < 0.0) {
    return false;
  }
  if (power_config_.max_wns_sacrifice > 0.0 &&
      candidate.wns_after < baseline.wns - power_config_.max_wns_sacrifice) {
    return false;
  }
  const double denom = std::abs(static_cast<double>(baseline.tns));
  if (power_config_.max_tns_sacrifice_ratio > 0.0 && denom > 1.0e-12) {
    const double tns_penalty =
        std::max(0.0, static_cast<double>(baseline.tns - candidate.tns_after));
    if (tns_penalty > denom * power_config_.max_tns_sacrifice_ratio) {
      return false;
    }
  }
  const double power_denom = positiveOrDefault(baseline.leakage_proxy, 1.0);
  const double power_drop_ratio = 100.0 * candidate.power_gain / power_denom;
  const double tns_expand_ratio =
      denom > 1.0e-12
          ? std::max(0.0,
                     std::abs(static_cast<double>(candidate.tns_after)) -
                         std::abs(static_cast<double>(candidate.tns_before))) /
                denom
          : 0.0;
  const double power_guard_score =
      power_drop_ratio * 90.0 - tns_expand_ratio - 30.0;
  if (power_guard_score <= power_config_.min_power_guard_score) {
    return false;
  }
  return true;
}

double PowerRecoveryPlusPolicy::scoreCandidate(const TrialCandidate &candidate,
                                               const Metrics &before,
                                               const Metrics &baseline) const {
  const double power_denom = positiveOrDefault(baseline.leakage_proxy, 1.0);
  const double tns_denom =
      positiveOrDefault(std::abs(static_cast<double>(baseline.tns)), 1.0e-9);
  const double normalized_power = candidate.power_gain / power_denom;
  const double tns_penalty =
      std::max(0.0, static_cast<double>(before.tns - candidate.tns_after)) /
      tns_denom;
  const double power_drop_ratio = 100.0 * normalized_power;
  const double power_guard_score = power_drop_ratio * 90.0 - tns_penalty - 30.0;
  if (power_guard_score <= power_config_.min_power_guard_score) {
    return -std::numeric_limits<double>::infinity();
  }
  const double crit_penalty = criticalityPenalty(candidate.target, candidate);
  const double innovus_bonus =
      candidate.kind == PowerRecoveryMoveKind::kRemoveBuffer
          ? power_config_.buffer_removal_bonus
      : candidate.kind == PowerRecoveryMoveKind::kSizeDown  ? 0.02
      : candidate.kind == PowerRecoveryMoveKind::kVtRecover ? 0.01
                                                            : 0.03;

  return power_config_.power_weight * normalized_power -
         power_config_.tns_weight * tns_penalty -
         power_config_.criticality_weight * crit_penalty + innovus_bonus;
}

bool PowerRecoveryPlusPolicy::commitCandidate(const TrialCandidate &candidate) {
  Target target;
  target.views = kInstanceView;
  target.driver_pin = candidate.target.output_pin;
  target.slack = candidate.target.worst_slack;

  PowerRecoveryCandidate move(
      resizer_, target, candidate.target.inst, candidate.target.output_pin,
      candidate.target.current_cell, candidate.candidate_cell, candidate.kind,
      candidate.score);
  const MoveResult result = committer_.commit(move);
  if (!result.accepted) {
    committer_.rejectPendingMoves();
    return false;
  }
  resizer_.updateParasiticsAndTiming();
  committer_.acceptPendingMoves();
  switch (candidate.kind) {
    case PowerRecoveryMoveKind::kSizeDown:
      ++accepted_size_down_;
      break;
    case PowerRecoveryMoveKind::kVtRecover:
      ++accepted_vt_recover_;
      break;
    case PowerRecoveryMoveKind::kSizeDownAndVtRecover:
      ++accepted_size_down_and_vt_;
      break;
    case PowerRecoveryMoveKind::kRemoveBuffer:
      ++accepted_remove_buffer_;
      break;
  }
  return true;
}

sta::Pin *PowerRecoveryPlusPolicy::findOutputPin(sta::Instance *inst) const {
  std::unique_ptr<sta::InstancePinIterator> pin_iter(
      network_->pinIterator(inst));
  while (pin_iter->hasNext()) {
    sta::Pin *pin = pin_iter->next();
    if (network_->direction(pin)->isAnyOutput() &&
        network_->net(pin) != nullptr) {
      return pin;
    }
  }
  return nullptr;
}

sta::Pin *PowerRecoveryPlusPolicy::findInputPin(sta::Instance *inst) const {
  std::unique_ptr<sta::InstancePinIterator> pin_iter(
      network_->pinIterator(inst));
  while (pin_iter->hasNext()) {
    sta::Pin *pin = pin_iter->next();
    if (network_->direction(pin)->isAnyInput() &&
        network_->net(pin) != nullptr) {
      return pin;
    }
  }
  return nullptr;
}

const sta::Pin *
PowerRecoveryPlusPolicy::findNetDriver(sta::Pin *load_pin) const {
  if (load_pin == nullptr) {
    return nullptr;
  }
  sta::Net *net = network_->net(load_pin);
  if (net == nullptr) {
    return nullptr;
  }

  std::unique_ptr<sta::NetConnectedPinIterator> pin_iter(
      network_->connectedPinIterator(net));
  while (pin_iter->hasNext()) {
    const sta::Pin *pin = pin_iter->next();
    if (pin != load_pin && network_->isDriver(pin)) {
      return pin;
    }
  }
  return nullptr;
}

float PowerRecoveryPlusPolicy::worstOutputSlack(sta::Instance *inst) const {
  float worst = std::numeric_limits<float>::max();
  std::unique_ptr<sta::InstancePinIterator> pin_iter(
      network_->pinIterator(inst));
  while (pin_iter->hasNext()) {
    sta::Pin *pin = pin_iter->next();
    if (!network_->direction(pin)->isAnyOutput()) {
      continue;
    }
    sta::Vertex *vertex = graph_->pinDrvrVertex(pin);
    if (vertex != nullptr) {
      const sta::Slack slack = sta_->slack(vertex, max_);
      if (slack < worst) {
        worst = slack;
      }
    }
  }
  return worst == std::numeric_limits<float>::max() ? 0.0f : worst;
}

int PowerRecoveryPlusPolicy::outputFanout(sta::Pin *output_pin) const {
  sta::Vertex *vertex = graph_->pinDrvrVertex(output_pin);
  if (vertex == nullptr) {
    return 0;
  }
  int fanout = 0;
  sta::VertexOutEdgeIterator edge_iter(vertex, graph_);
  while (edge_iter.hasNext()) {
    edge_iter.next();
    ++fanout;
  }
  return fanout;
}

bool PowerRecoveryPlusPolicy::passesBufferRemovalFanoutGuard(
    const TargetInfo &target) const {
  sta::Pin *input_pin = findInputPin(target.inst);
  const sta::Pin *upstream_driver = findNetDriver(input_pin);
  if (upstream_driver == nullptr) {
    return false;
  }

  sta_->checkFanoutPreamble();
  float fanout = 0.0f;
  float limit = 0.0f;
  float slack = 0.0f;
  sta_->checkFanout(upstream_driver, sta_->cmdMode(), max_, fanout, limit,
                    slack);

  const float new_fanout = fanout + target.fanout - 1.0f;
  if (limit > 0.0f && new_fanout > limit) {
    return false;
  }
  if (power_config_.max_unbuffer_result_fanout > 0 &&
      new_fanout > power_config_.max_unbuffer_result_fanout) {
    return false;
  }
  return true;
}

double PowerRecoveryPlusPolicy::inputCapProxy(sta::LibertyCell *cell) const {
  double cap = 0.0;
  std::unique_ptr<sta::LibertyCellPortIterator> port_iter(
      new sta::LibertyCellPortIterator(cell));
  while (port_iter->hasNext()) {
    sta::LibertyPort *port = port_iter->next();
    if (port != nullptr && port->direction()->isAnyInput()) {
      cap += port->capacitance();
    }
  }
  return cap;
}

double PowerRecoveryPlusPolicy::cellArea(sta::LibertyCell *cell) const {
  odb::dbMaster *master = resizer_.dbNetwork()->staToDb(cell);
  return master != nullptr ? static_cast<double>(master->getArea()) : 0.0;
}

double PowerRecoveryPlusPolicy::cellLeakage(sta::LibertyCell *cell) const {
  return resizer_.cellLeakage(cell).value_or(0.0f);
}

bool PowerRecoveryPlusPolicy::footprintOk(sta::LibertyCell *current,
                                          sta::LibertyCell *candidate) const {
  if (!power_config_.match_cell_footprint) {
    return true;
  }
  odb::dbMaster *curr_master = resizer_.dbNetwork()->staToDb(current);
  odb::dbMaster *cand_master = resizer_.dbNetwork()->staToDb(candidate);
  if (curr_master == nullptr || cand_master == nullptr) {
    return false;
  }
  return cand_master->getHeight() == curr_master->getHeight() &&
         cand_master->getWidth() <= curr_master->getWidth();
}

bool PowerRecoveryPlusPolicy::isPowerDirectionVtSwap(
    sta::LibertyCell *current, sta::LibertyCell *candidate) const {
  odb::dbMaster *curr_master = resizer_.dbNetwork()->staToDb(current);
  odb::dbMaster *cand_master = resizer_.dbNetwork()->staToDb(candidate);
  if (curr_master == nullptr || cand_master == nullptr ||
      resizer_.cellVTType(curr_master) == resizer_.cellVTType(cand_master)) {
    return true;
  }
  // Power-direction VT recovery must reduce leakage.  This explicitly rejects
  // setup-recovery style swaps such as R/L -> SL even if they also change area
  // or pin capacitance.  Missing/flat leakage data is treated conservatively
  // for VT-only changes; size-down-with-VT can still pass through area/cap only
  // if it is not a VT direction change.
  return cellLeakage(candidate) + 1.0e-18 < cellLeakage(current);
}

PowerRecoveryMoveKind
PowerRecoveryPlusPolicy::classifyMove(sta::LibertyCell *current,
                                      sta::LibertyCell *candidate) const {
  odb::dbMaster *curr_master = resizer_.dbNetwork()->staToDb(current);
  odb::dbMaster *cand_master = resizer_.dbNetwork()->staToDb(candidate);
  const bool vt_diff =
      curr_master != nullptr && cand_master != nullptr &&
      resizer_.cellVTType(curr_master) != resizer_.cellVTType(cand_master);
  const bool physically_smaller =
      cellArea(candidate) < cellArea(current) ||
      inputCapProxy(candidate) < inputCapProxy(current);
  const bool weaker = resizer_.cellDriveResistance(candidate) >
                      resizer_.cellDriveResistance(current);
  if (vt_diff && weaker && physically_smaller) {
    return PowerRecoveryMoveKind::kSizeDownAndVtRecover;
  }
  if (vt_diff) {
    return PowerRecoveryMoveKind::kVtRecover;
  }
  return PowerRecoveryMoveKind::kSizeDown;
}

bool PowerRecoveryPlusPolicy::isUsefulPowerCandidate(
    sta::LibertyCell *current, sta::LibertyCell *candidate,
    const PowerRecoveryMoveKind kind) const {
  const double leakage_gain = cellLeakage(current) - cellLeakage(candidate);
  const double area_gain = cellArea(current) - cellArea(candidate);
  const double cap_gain = inputCapProxy(current) - inputCapProxy(candidate);
  if (power_config_.require_leakage_gain && leakage_gain < 0.0) {
    return false;
  }
  if ((kind == PowerRecoveryMoveKind::kVtRecover ||
       kind == PowerRecoveryMoveKind::kSizeDownAndVtRecover) &&
      !isPowerDirectionVtSwap(current, candidate)) {
    return false;
  }
  if (kind == PowerRecoveryMoveKind::kSizeDown) {
    return resizer_.cellDriveResistance(candidate) >
               resizer_.cellDriveResistance(current) &&
           (area_gain > 0.0 || cap_gain > 0.0 || leakage_gain > 0.0);
  }
  return leakage_gain > 0.0 || area_gain > 0.0 || cap_gain > 0.0;
}

double PowerRecoveryPlusPolicy::criticalityPenalty(
    const TargetInfo &target, const TrialCandidate &candidate) const {
  double penalty = 0.0;
  if (target.worst_slack < 0.0) {
    penalty += std::min(1.0, -static_cast<double>(target.worst_slack) * 10.0);
  }
  if (target.fanout > 12) {
    penalty += std::min(0.5, (target.fanout - 12) * 0.02);
  }
  if (candidate.wns_after < 0.0 && candidate.wns_before >= 0.0) {
    penalty += 0.5;
  }
  return penalty;
}

void PowerRecoveryPlusPolicy::recordReject(const std::string &reason) {
  const std::string key = reason.empty() ? std::string("unknown") : reason;
  ++reject_reason_counts_[key];
}

std::string PowerRecoveryPlusPolicy::rejectReasonSummary() const {
  if (reject_reason_counts_.empty()) {
    return "{}";
  }
  std::vector<std::pair<std::string, int>> reasons(reject_reason_counts_.begin(),
                                                   reject_reason_counts_.end());
  std::ranges::sort(reasons, [](const auto &lhs, const auto &rhs) {
    if (lhs.second != rhs.second) {
      return lhs.second > rhs.second;
    }
    return lhs.first < rhs.first;
  });
  std::ostringstream out;
  out << "{";
  const size_t limit = std::min<size_t>(reasons.size(), 8);
  for (size_t i = 0; i < limit; ++i) {
    if (i > 0) {
      out << ",";
    }
    out << reasons[i].first << ":" << reasons[i].second;
  }
  if (reasons.size() > limit) {
    out << ",...";
  }
  out << "}";
  return out.str();
}

std::string PowerRecoveryPlusPolicy::instName(sta::Instance *inst) const {
  return inst != nullptr ? network_->pathName(inst) : std::string("<null>");
}

} // namespace rsz
