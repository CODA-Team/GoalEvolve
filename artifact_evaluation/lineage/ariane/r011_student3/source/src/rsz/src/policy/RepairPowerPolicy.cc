// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "RepairPowerPolicy.hh"

#include <algorithm>
#include <cmath>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_set>

#include "MoveCommitter.hh"
#include "db_sta/dbNetwork.hh"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/Graph.hh"
#include "sta/GraphClass.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/PortDirection.hh"
#include "sta/Sta.hh"
#include "utl/Logger.h"
#include "utl/env.h"

namespace rsz {

using utl::RSZ;

namespace {

bool envSet(const char *name) { return std::getenv(name) != nullptr; }

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

double positiveOrDefault(const double value, const double fallback) {
  return value > 0.0 ? value : fallback;
}

bool tnsWorsenedBeyond(const sta::Slack before, const sta::Slack after,
                       const double ratio, const double absolute_floor,
                       const double absolute_cap) {
  const double before_value = static_cast<double>(before);
  const double after_value = static_cast<double>(after);
  const double allowed = std::min(
      absolute_cap,
      std::max(absolute_floor, std::abs(before_value) * std::max(0.0, ratio)));
  return after_value < before_value - allowed;
}

double slackBonus(const double slack, const double scale) {
  if (!std::isfinite(slack) || slack <= 0.0) {
    return 0.0;
  }
  return std::min(slack, 2.0e-10) * scale;
}

} // namespace

RepairPowerPolicy::RepairPowerPolicy(Resizer &resizer, MoveCommitter &committer,
                                     RepairSetupContext &setup_context,
                                     const OptimizerRunConfig &config)
    : OptimizationPolicy(resizer, committer, setup_context, config) {
  is_experimental = true;
}

RepairPowerPolicy::~RepairPowerPolicy() = default;

bool RepairPowerPolicy::start() {
  OptimizationPolicy::start();
  loadConfig();
  if (cfg_.late_second_pass) {
    logger_->report("METRIC|repair_power_late_second_pass_started|1");
  }
  baseline_ = collectMetrics();
  sta_->checkCapacitancesPreamble(sta_->scenes());
  sta_->checkSlewsPreamble();
  sta_->checkFanoutPreamble();
  if (cfg_.phase == "mid_area_reclaim" &&
      std::abs(static_cast<double>(baseline_.tns)) < 20.0e-9) {
    cfg_.max_moves = std::min(cfg_.max_moves, 500);
    cfg_.trial_limit =
        std::min(cfg_.trial_limit, std::max(1000, cfg_.max_moves * 6));
    cfg_.batch_size = std::min(cfg_.batch_size, 128);
    cfg_.timing_update_interval = std::min(cfg_.timing_update_interval, 8);
    cfg_.max_timing_update_interval =
        std::min(cfg_.max_timing_update_interval, cfg_.timing_update_interval);
    cfg_.min_timing_update_interval =
        std::min(cfg_.min_timing_update_interval, cfg_.timing_update_interval);
    cfg_.full_metrics_interval = std::min(cfg_.full_metrics_interval, 32);
  }
  committed_ = 0;
  trials_ = 0;
  rejected_ = 0;
  accepted_size_down_ = 0;
  accepted_power_vt_swap_ = 0;
  accepted_size_down_vt_ = 0;
  accepted_remove_buffer_ = 0;
  adaptive_window_size_ = cfg_.timing_update_interval;
  consecutive_window_successes_ = 0;
  captureCriticalTimingCone();
  touched_.clear();
  reject_reasons_.clear();

  logger_->info(RSZ, 2320,
                "REPAIR_POWER|start|phase={}|proportion={:.3g}|wns={}|tns={}|"
                "area={:.6g}|leakage_proxy={:.6g}|fanout_violations={}|"
                "max_moves={}|max_targets={}|trial_limit={}|batch_size={}|"
                "timing_update_interval={}|min_timing_update_interval={}|"
                "max_timing_update_interval={}|adaptive_window={}|"
                "full_metrics_interval={}|"
                "max_tns_expand_ratio={:.3g}|max_wns_drop={}|"
                "vt_swap={}|skip_seq={}",
                cfg_.phase, cfg_.proportion,
                sta::delayAsString(baseline_.wns, 3, sta_),
                sta::delayAsString(baseline_.tns, 3, sta_), baseline_.area,
                baseline_.leakage, baseline_.fanout_violations, cfg_.max_moves,
                cfg_.max_targets, cfg_.trial_limit, cfg_.batch_size,
                cfg_.timing_update_interval, cfg_.min_timing_update_interval,
                cfg_.max_timing_update_interval,
                cfg_.adaptive_timing_window ? "true" : "false",
                cfg_.full_metrics_interval, cfg_.max_tns_expand_ratio,
                sta::delayAsString(cfg_.max_wns_drop, 3, sta_),
                cfg_.enable_vt_swap ? "true" : "false",
                cfg_.skip_sequential ? "true" : "false");
  return true;
}

void RepairPowerPolicy::loadConfig() {
  cfg_.late_second_pass =
      config_.power_phase == "late_leakage_recovery_second_pass";
  cfg_.phase = cfg_.late_second_pass
                   ? std::string("late_leakage_recovery")
                   : (config_.power_phase.empty()
                          ? std::string("early_forced_reclaim")
                          : config_.power_phase);
  cfg_.proportion = std::clamp(config_.power_proportion, 0.0f, 1.0f);
  cfg_.match_cell_footprint = config_.match_cell_footprint;
  cfg_.verbose = config_.verbose;

  cfg_.max_moves = utl::readEnvarNonNegativeInt("RSZ_REPAIR_POWER_MAX_MOVES",
                                                cfg_.max_moves);
  cfg_.max_targets = utl::readEnvarNonNegativeInt(
      "RSZ_REPAIR_POWER_MAX_TARGETS", cfg_.max_targets);
  cfg_.trial_limit = utl::readEnvarNonNegativeInt(
      "RSZ_REPAIR_POWER_TRIAL_LIMIT", cfg_.trial_limit);
  cfg_.batch_size =
      std::max(1, utl::readEnvarNonNegativeInt("RSZ_REPAIR_POWER_BATCH_SIZE",
                                               cfg_.batch_size));
  cfg_.max_unbuffer_fanout = utl::readEnvarNonNegativeInt(
      "RSZ_REPAIR_POWER_MAX_UNBUFFER_FANOUT", cfg_.max_unbuffer_fanout);
  cfg_.max_unbuffer_result_fanout = utl::readEnvarNonNegativeInt(
      "RSZ_REPAIR_POWER_MAX_UNBUFFER_RESULT_FANOUT",
      cfg_.max_unbuffer_result_fanout);
  cfg_.max_unbuffer_moves = utl::readEnvarNonNegativeInt(
      "RSZ_REPAIR_POWER_MAX_UNBUFFER_MOVES", cfg_.max_unbuffer_moves);
  cfg_.max_power_vt_swaps = utl::readEnvarNonNegativeInt(
      "RSZ_REPAIR_POWER_MAX_POWER_VT_SWAPS", cfg_.max_power_vt_swaps);
  cfg_.max_revisit_passes = utl::readEnvarNonNegativeInt(
      "RSZ_REPAIR_POWER_REVISIT_PASSES", cfg_.max_revisit_passes);
  cfg_.timing_update_interval = std::max(
      1, utl::readEnvarNonNegativeInt("RSZ_REPAIR_POWER_TIMING_UPDATE_INTERVAL",
                                      cfg_.timing_update_interval));
  cfg_.min_timing_update_interval =
      std::max(1, utl::readEnvarNonNegativeInt(
                      "RSZ_REPAIR_POWER_TIMING_UPDATE_MIN_INTERVAL",
                      cfg_.min_timing_update_interval));
  cfg_.max_timing_update_interval =
      std::max(1, utl::readEnvarNonNegativeInt(
                      "RSZ_REPAIR_POWER_TIMING_UPDATE_MAX_INTERVAL",
                      cfg_.max_timing_update_interval));
  cfg_.adaptive_success_grow_threshold = std::max(
      1,
      utl::readEnvarNonNegativeInt("RSZ_REPAIR_POWER_ADAPTIVE_GROW_THRESHOLD",
                                   cfg_.adaptive_success_grow_threshold));
  cfg_.full_metrics_interval = std::max(
      1, utl::readEnvarNonNegativeInt("RSZ_REPAIR_POWER_FULL_METRICS_INTERVAL",
                                      cfg_.full_metrics_interval));
  cfg_.min_target_slack =
      readDoubleEnv("RSZ_REPAIR_POWER_MIN_TARGET_SLACK", cfg_.min_target_slack);
  cfg_.min_unbuffer_slack = readDoubleEnv("RSZ_REPAIR_POWER_MIN_UNBUFFER_SLACK",
                                          cfg_.min_unbuffer_slack);
  cfg_.max_tns_expand_ratio = readDoubleEnv(
      "RSZ_REPAIR_POWER_MAX_TNS_EXPAND_RATIO", cfg_.max_tns_expand_ratio);
  cfg_.max_wns_drop =
      readDoubleEnv("RSZ_REPAIR_POWER_MAX_WNS_DROP", cfg_.max_wns_drop);
  cfg_.late_vt_min_target_slack =
      readDoubleEnv("RSZ_REPAIR_POWER_LATE_VT_MIN_TARGET_SLACK",
                    cfg_.late_vt_min_target_slack);
  cfg_.late_protected_slack = readDoubleEnv(
      "RSZ_REPAIR_POWER_LATE_PROTECTED_SLACK", cfg_.late_protected_slack);
  cfg_.late_min_power_guard_score =
      readDoubleEnv("RSZ_REPAIR_POWER_LATE_MIN_POWER_GUARD_SCORE",
                    cfg_.late_min_power_guard_score);
  cfg_.late_protected_fanout = std::max(
      1, utl::readEnvarNonNegativeInt("RSZ_REPAIR_POWER_LATE_PROTECTED_FANOUT",
                                      cfg_.late_protected_fanout));
  cfg_.leakage_weight =
      readDoubleEnv("RSZ_REPAIR_POWER_LEAKAGE_WEIGHT", cfg_.leakage_weight);
  cfg_.area_weight =
      readDoubleEnv("RSZ_REPAIR_POWER_AREA_WEIGHT", cfg_.area_weight);
  cfg_.input_cap_weight =
      readDoubleEnv("RSZ_REPAIR_POWER_INPUT_CAP_WEIGHT", cfg_.input_cap_weight);
  cfg_.criticality_weight =
      readDoubleEnv("RSZ_REPAIR_POWER_CRIT_WEIGHT", cfg_.criticality_weight);
  cfg_.buffer_bonus =
      readDoubleEnv("RSZ_REPAIR_POWER_BUFFER_BONUS", cfg_.buffer_bonus);
  cfg_.pure_vt_min_leakage_ratio = readDoubleEnv(
      "RSZ_REPAIR_POWER_PURE_VT_MIN_LEAK_RATIO",
      cfg_.pure_vt_min_leakage_ratio);
  cfg_.pure_vt_min_cap_gain = readDoubleEnv(
      "RSZ_REPAIR_POWER_PURE_VT_MIN_CAP_GAIN", cfg_.pure_vt_min_cap_gain);
  cfg_.skip_sequential = readBoolEnv("RSZ_REPAIR_POWER_SKIP_SEQUENTIAL", true);
  cfg_.enable_vt_swap = readBoolEnv("RSZ_REPAIR_POWER_ENABLE_VT_SWAP", true);
  cfg_.adaptive_timing_window = readBoolEnv("RSZ_REPAIR_POWER_ADAPTIVE_WINDOW",
                                            cfg_.adaptive_timing_window);

  if (config_.power_max_moves > 0) {
    cfg_.max_moves = config_.power_max_moves;
  }
  if (config_.power_max_tns_expand_ratio > 0.0) {
    cfg_.max_tns_expand_ratio = config_.power_max_tns_expand_ratio;
  }
  if (config_.power_max_wns_drop > 0.0) {
    cfg_.max_wns_drop = config_.power_max_wns_drop;
  }
  cfg_.authoritative_timing_budget =
      config_.phases.empty() && cfg_.phase == "late_leakage_recovery"
      && (config_.power_max_tns_expand_ratio > 0.0
          || config_.power_max_wns_drop > 0.0);

  if (cfg_.phase == "early_forced_reclaim") {
    // Contest hard constraints only allow combinational logic changes.  Do not
    // let environment overrides include FF/sequential cells in this phase.
    if (!cfg_.authoritative_timing_budget) {
      cfg_.max_tns_expand_ratio = std::max(cfg_.max_tns_expand_ratio, 1.0);
      cfg_.max_wns_drop = std::max(cfg_.max_wns_drop, 8.0e-10);
    }
    cfg_.skip_sequential = true;
    const int eligible = std::max(1, eligibleLogicInstanceCount());
    const int proportional_moves =
        std::max(25, static_cast<int>(eligible * cfg_.proportion * 0.95f));
    if (!envSet("RSZ_REPAIR_POWER_MAX_MOVES") && config_.power_max_moves <= 0) {
      cfg_.max_moves = std::min(proportional_moves, 12500);
      cfg_.bounded_early_tail = proportional_moves > 12500;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_TARGETS")) {
      cfg_.max_targets =
          std::min(std::max(1000, static_cast<int>(eligible * cfg_.proportion)),
                   std::min(eligible, 14000));
    }
    if (!envSet("RSZ_REPAIR_POWER_TRIAL_LIMIT")) {
      cfg_.trial_limit = std::min(std::max(cfg_.max_moves * 3, 2000), 36000);
    }
    if (!envSet("RSZ_REPAIR_POWER_BATCH_SIZE")) {
      cfg_.batch_size = std::min(cfg_.max_moves, 512);
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_UNBUFFER_MOVES")) {
      cfg_.max_unbuffer_moves =
          std::min(600, std::max(50, cfg_.max_moves / 20));
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_POWER_VT_SWAPS")) {
      cfg_.max_power_vt_swaps =
          std::min(8000, std::max(600, cfg_.max_moves * 2 / 5));
    }
    if (!envSet("RSZ_REPAIR_POWER_REVISIT_PASSES")) {
      cfg_.max_revisit_passes = 1;
    }
    if (!envSet("RSZ_REPAIR_POWER_TIMING_UPDATE_INTERVAL")) {
      cfg_.timing_update_interval = 16;
    }
    if (!envSet("RSZ_REPAIR_POWER_TIMING_UPDATE_MIN_INTERVAL")) {
      cfg_.min_timing_update_interval = 4;
    }
    if (!envSet("RSZ_REPAIR_POWER_TIMING_UPDATE_MAX_INTERVAL")) {
      cfg_.max_timing_update_interval = 16;
    }
    if (!envSet("RSZ_REPAIR_POWER_ADAPTIVE_GROW_THRESHOLD")) {
      cfg_.adaptive_success_grow_threshold = 24;
    }
    if (!envSet("RSZ_REPAIR_POWER_FULL_METRICS_INTERVAL")) {
      cfg_.full_metrics_interval = 256;
    }
    if (!envSet("RSZ_REPAIR_POWER_MIN_TARGET_SLACK")) {
      cfg_.min_target_slack = -1.0e-8;
    }
    if (!envSet("RSZ_REPAIR_POWER_MIN_UNBUFFER_SLACK")) {
      cfg_.min_unbuffer_slack = 0.0;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_TNS_EXPAND_RATIO") &&
        config_.power_max_tns_expand_ratio <= 0.0) {
      cfg_.max_tns_expand_ratio = 6.0;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_WNS_DROP") &&
        config_.power_max_wns_drop <= 0.0) {
      cfg_.max_wns_drop = 5.0e-10;
    }
    // Power-direction VT swaps are allowed only when they also reduce leakage;
    // timing VT swaps are rejected in usefulCandidate().
    cfg_.enable_vt_swap = readBoolEnv("RSZ_REPAIR_POWER_ENABLE_VT_SWAP", true);
  } else if (cfg_.phase == "mid_area_reclaim") {
    const int eligible = std::max(1, eligibleLogicInstanceCount());
    if (!envSet("RSZ_REPAIR_POWER_MAX_MOVES") && config_.power_max_moves <= 0) {
      cfg_.max_moves = std::min(
          std::max(50, static_cast<int>(eligible * cfg_.proportion * 0.025f)),
          2500);
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_TARGETS")) {
      cfg_.max_targets = std::min(
          std::max(800, static_cast<int>(eligible * cfg_.proportion * 0.60f)),
          12000);
    }
    if (!envSet("RSZ_REPAIR_POWER_TRIAL_LIMIT")) {
      cfg_.trial_limit = std::min(std::max(cfg_.max_moves * 5, 1000), 12000);
    }
    if (!envSet("RSZ_REPAIR_POWER_BATCH_SIZE")) {
      cfg_.batch_size = std::min(cfg_.max_moves, 256);
    }
    if (!envSet("RSZ_REPAIR_POWER_MIN_TARGET_SLACK")) {
      cfg_.min_target_slack = 2.0e-11;
    }
    if (!envSet("RSZ_REPAIR_POWER_FULL_METRICS_INTERVAL")) {
      cfg_.full_metrics_interval = 128;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_TNS_EXPAND_RATIO") &&
        config_.power_max_tns_expand_ratio <= 0.0) {
      cfg_.max_tns_expand_ratio = 0.02;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_WNS_DROP") &&
        config_.power_max_wns_drop <= 0.0) {
      cfg_.max_wns_drop = 1.0e-11;
    }
    cfg_.max_unbuffer_moves = 0;
    cfg_.max_unbuffer_fanout = 0;
    cfg_.skip_sequential = true;
    cfg_.enable_vt_swap = true;
  } else if (cfg_.phase == "late_leakage_recovery") {
    const int eligible = std::max(1, eligibleLogicInstanceCount());
    if (!envSet("RSZ_REPAIR_POWER_MAX_MOVES") && config_.power_max_moves <= 0) {
      cfg_.max_moves = std::min(
          std::max(800, static_cast<int>(eligible * cfg_.proportion * 0.36f)),
          4200);
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_TARGETS")) {
      cfg_.max_targets = std::min(
          std::max(1200, static_cast<int>(eligible * cfg_.proportion * 0.95f)),
          20000);
    }
    if (!envSet("RSZ_REPAIR_POWER_TRIAL_LIMIT")) {
      cfg_.trial_limit = std::min(std::max(cfg_.max_moves * 8, 4000), 36000);
    }
    if (!envSet("RSZ_REPAIR_POWER_BATCH_SIZE")) {
      cfg_.batch_size = std::min(cfg_.max_moves, 192);
    }
    if (!envSet("RSZ_REPAIR_POWER_TIMING_UPDATE_INTERVAL")) {
      cfg_.timing_update_interval = 8;
    }
    if (!envSet("RSZ_REPAIR_POWER_TIMING_UPDATE_MIN_INTERVAL")) {
      cfg_.min_timing_update_interval = 4;
    }
    if (!envSet("RSZ_REPAIR_POWER_TIMING_UPDATE_MAX_INTERVAL")) {
      cfg_.max_timing_update_interval = 16;
    }
    if (!envSet("RSZ_REPAIR_POWER_MIN_TARGET_SLACK")) {
      cfg_.min_target_slack = -4.0e-10;
    }
    if (!envSet("RSZ_REPAIR_POWER_LATE_VT_MIN_TARGET_SLACK")) {
      cfg_.late_vt_min_target_slack = -4.0e-10;
    }
    if (!envSet("RSZ_REPAIR_POWER_LATE_PROTECTED_SLACK")) {
      cfg_.late_protected_slack = -4.0e-10;
    }
    if (!envSet("RSZ_REPAIR_POWER_MIN_UNBUFFER_SLACK")) {
      cfg_.min_unbuffer_slack = 1.2e-10;
    }
    if (!envSet("RSZ_REPAIR_POWER_FULL_METRICS_INTERVAL")) {
      cfg_.full_metrics_interval = 64;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_TNS_EXPAND_RATIO") &&
        config_.power_max_tns_expand_ratio <= 0.0) {
      cfg_.max_tns_expand_ratio = 1.0;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_WNS_DROP") &&
        config_.power_max_wns_drop <= 0.0) {
      cfg_.max_wns_drop = 8.0e-10;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_UNBUFFER_MOVES")) {
      cfg_.max_unbuffer_moves =
          std::min(200, std::max(10, cfg_.max_moves / 20));
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_UNBUFFER_FANOUT")) {
      cfg_.max_unbuffer_fanout = 2;
    }
    if (!envSet("RSZ_REPAIR_POWER_MAX_UNBUFFER_RESULT_FANOUT")) {
      cfg_.max_unbuffer_result_fanout = 4;
    }
    if (!envSet("RSZ_REPAIR_POWER_LATE_PROTECTED_FANOUT")) {
      cfg_.late_protected_fanout = 1000000;
    }
    cfg_.skip_sequential = true;
    cfg_.enable_vt_swap = true;
  }

  cfg_.min_timing_update_interval =
      std::min(cfg_.min_timing_update_interval, cfg_.timing_update_interval);
  cfg_.max_timing_update_interval =
      std::max(cfg_.max_timing_update_interval, cfg_.timing_update_interval);
  cfg_.timing_update_interval =
      std::clamp(cfg_.timing_update_interval, cfg_.min_timing_update_interval,
                 cfg_.max_timing_update_interval);
}

int RepairPowerPolicy::eligibleLogicInstanceCount() const {
  int count = 0;
  sta::LeafInstanceIterator *iter = network_->leafInstanceIterator();
  while (iter->hasNext()) {
    sta::Instance *inst = iter->next();
    sta::LibertyCell *cell = network_->libertyCell(inst);
    if (cell != nullptr && isContestModifiableInstance(inst)) {
      ++count;
    }
  }
  delete iter;
  return count;
}

RepairPowerPolicy::Metrics RepairPowerPolicy::collectMetrics() const {
  Metrics metrics;
  sta::Vertex *worst = nullptr;
  sta_->worstSlack(max_, metrics.wns, worst);
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
      metrics.leakage += cellLeakage(cell);
    }
  }
  delete iter;

  sta_->checkFanoutPreamble();
  sta::LeafInstanceIterator *fanout_iter = network_->leafInstanceIterator();
  while (fanout_iter->hasNext()) {
    sta::Instance *inst = fanout_iter->next();
    sta::Pin *output_pin = findOutputPin(inst);
    if (output_pin == nullptr) {
      continue;
    }
    float fanout = 0.0f;
    float limit = 0.0f;
    float slack = 0.0f;
    sta_->checkFanout(output_pin, sta_->cmdMode(), max_, fanout, limit, slack);
    if (limit > 0.0f && fanout > limit) {
      ++metrics.fanout_violations;
    }
  }
  delete fanout_iter;
  return metrics;
}

RepairPowerPolicy::Metrics RepairPowerPolicy::collectTimingMetrics() const {
  Metrics metrics;
  sta::Vertex *worst = nullptr;
  sta_->worstSlack(max_, metrics.wns, worst);
  metrics.tns = sta_->totalNegativeSlack(max_);
  metrics.area = 0.0;
  metrics.leakage = 0.0;
  metrics.fanout_violations = baseline_.fanout_violations;
  return metrics;
}

void RepairPowerPolicy::iterate() {
  if (converged_) {
    return;
  }

  if (cfg_.phase == "mid_area_reclaim") {
    iterateMidAreaReclaim();
    return;
  }
  if (cfg_.phase == "late_leakage_recovery") {
    iterateLateLeakageRecovery();
    return;
  }
  iterateEarlyForcedReclaim();
}

void RepairPowerPolicy::iterateEarlyForcedReclaim() {
  constexpr int tail_retained_limit = 256;
  constexpr int tail_examined_limit = 512;
  const int retained_move_cap = cfg_.max_moves;
  bool changed = false;
  Metrics current = collectMetrics();
  int revisit_passes = 0;
  int tail_examined = 0;
  int tail_retained = 0;
  const auto moveLimitReached = [&]() {
    if (committed_ < retained_move_cap) {
      return false;
    }
    return !cfg_.bounded_early_tail ||
           tail_retained >= tail_retained_limit ||
           tail_examined >= tail_examined_limit;
  };
  const auto trialLimitReached = [&]() {
    if (trials_ < cfg_.trial_limit) {
      return false;
    }
    return committed_ < retained_move_cap ||
           trials_ >= cfg_.trial_limit + tail_examined_limit;
  };
  while (!moveLimitReached() && !trialLimitReached()) {
    std::vector<Candidate> candidates;
    for (const Target &target : collectTargets()) {
      for (Candidate &candidate : generateCandidates(target)) {
        candidate.score = scoreCandidate(candidate);
        if (candidate.score > 0.0) {
          candidates.push_back(candidate);
        } else {
          ++rejected_;
          recordReject("score");
        }
      }
      if (static_cast<int>(candidates.size()) >= cfg_.batch_size ||
          trialLimitReached()) {
        break;
      }
    }
    if (candidates.empty()) {
      if (committed_ >= retained_move_cap) {
        break;
      }
      if (revisit_passes < cfg_.max_revisit_passes && !touched_.empty()) {
        ++revisit_passes;
        touched_.clear();
        logger_->info(RSZ, 2338,
                      "REPAIR_POWER|revisit|phase={}|pass={}|reason=no_candidates",
                      cfg_.phase, revisit_passes);
        continue;
      }
      break;
    }
    std::ranges::sort(candidates, [this](const Candidate &lhs,
                                         const Candidate &rhs) {
      return stabilitySampledPreferred(lhs, rhs);
    });
    markStabilitySampledTies(candidates);

    int accepted_this_batch = 0;
    for (size_t i = 0; i < candidates.size();) {
      const Candidate &candidate = candidates[i];
      if (moveLimitReached() || trialLimitReached() ||
          accepted_this_batch >= cfg_.batch_size) {
        break;
      }
      if (touched_.contains(candidate.target.inst)) {
        ++i;
        continue;
      }
      const int active_window_size
          = committed_ >= retained_move_cap
                ? 1
                : (cfg_.adaptive_timing_window ? adaptive_window_size_
                                               : cfg_.timing_update_interval);
      if (active_window_size > 1 &&
          !candidateNeedsImmediateTimingGuard(candidate)) {
        std::vector<Candidate> window;
        std::unordered_set<sta::Instance *> window_insts;
        size_t j = i;
        while (j < candidates.size() &&
               static_cast<int>(window.size()) < active_window_size &&
               committed_ + accepted_this_batch +
                       static_cast<int>(window.size()) <
                   cfg_.max_moves &&
               trials_ < cfg_.trial_limit &&
               accepted_this_batch + static_cast<int>(window.size()) <
                   cfg_.batch_size) {
          const Candidate &window_candidate = candidates[j];
          if (touched_.contains(window_candidate.target.inst) ||
              window_insts.contains(window_candidate.target.inst)) {
            ++j;
            continue;
          }
          if (excludeCriticalConeReversion(window_candidate)) {
            ++trials_;
            ++rejected_;
            recordReject("critical_cone_reversion");
            logger_->report("METRIC|repair_power_reversion_excluded|1");
            touched_.insert(window_candidate.target.inst);
            ++j;
            continue;
          }
          if (candidateNeedsImmediateTimingGuard(window_candidate)) {
            if (window.empty()) {
              break;
            }
            break;
          }
          ++trials_;
          window.push_back(window_candidate);
          window_insts.insert(window_candidate.target.inst);
          ++j;
        }
        if (window.empty()) {
          ++i;
          continue;
        }
        if (tryCommitCandidateWindow(window, current)) {
          changed = true;
          committed_ += window.size();
          accepted_this_batch += window.size();
          noteAdaptiveWindowSuccess(window.size());
        } else {
          recordReject("window_split_retry");
          noteAdaptiveWindowFailure("window_split_retry");
          int split_rejects = 0;
          for (const Candidate &window_candidate : window) {
            if (committed_ >= cfg_.max_moves || trials_ >= cfg_.trial_limit ||
                accepted_this_batch >= cfg_.batch_size) {
              break;
            }
            if (tryCommitCandidate(window_candidate, current)) {
              changed = true;
              ++committed_;
              ++accepted_this_batch;
            } else {
              ++rejected_;
              ++split_rejects;
            }
          }
          if (split_rejects * 2 >= static_cast<int>(window.size())) {
            noteAdaptiveWindowFailure("split_retry_rejects");
          }
        }
        for (const Candidate &window_candidate : window) {
          touched_.insert(window_candidate.target.inst);
        }
        i = j;
        continue;
      }
      const bool tail_candidate = committed_ >= retained_move_cap;
      if (tail_candidate) {
        ++tail_examined;
        logger_->report("METRIC|repair_power_tail_examined|1");
      }
      ++trials_;
      if (excludeCriticalConeReversion(candidate)) {
        ++rejected_;
        recordReject("critical_cone_reversion");
        logger_->report("METRIC|repair_power_reversion_excluded|1");
        touched_.insert(candidate.target.inst);
        ++i;
        continue;
      }
      if (tryCommitCandidate(candidate, current)) {
        changed = true;
        ++committed_;
        ++accepted_this_batch;
        if (tail_candidate) {
          ++tail_retained;
          logger_->report("METRIC|repair_power_tail_retained|1");
        }
        touched_.insert(candidate.target.inst);
      } else {
        ++rejected_;
        touched_.insert(candidate.target.inst);
      }
      ++i;
    }
    if (accepted_this_batch == 0) {
      if (committed_ >= retained_move_cap) {
        break;
      }
      if (revisit_passes < cfg_.max_revisit_passes && !touched_.empty()) {
        ++revisit_passes;
        touched_.clear();
        logger_->info(RSZ, 2339,
                      "REPAIR_POWER|revisit|phase={}|pass={}|reason=no_accept",
                      cfg_.phase, revisit_passes);
        continue;
      }
      break;
    }
  }

  const Metrics final_metrics = collectMetrics();
  logger_->info(
      RSZ, 2321,
      "REPAIR_POWER|summary|phase={}|committed={}|trials={}|rejected={}|"
      "accepted_size_down={}|accepted_power_vt_swap={}|"
      "accepted_size_down_vt={}|accepted_remove_buffer={}|"
      "final_adaptive_window={}|"
      "wns_before={}|wns_after={}|tns_before={}|tns_after={}|"
      "leakage_before={:.6g}|leakage_after={:.6g}|leakage_delta={:.6g}|"
      "area_before={:.6g}|area_after={:.6g}|fanout_violations_before={}|"
      "fanout_violations_after={}|reject_reasons={}",
      cfg_.phase, committed_, trials_, rejected_, accepted_size_down_,
      accepted_power_vt_swap_, accepted_size_down_vt_, accepted_remove_buffer_,
      adaptive_window_size_, sta::delayAsString(baseline_.wns, 3, sta_),
      sta::delayAsString(final_metrics.wns, 3, sta_),
      sta::delayAsString(baseline_.tns, 3, sta_),
      sta::delayAsString(final_metrics.tns, 3, sta_), baseline_.leakage,
      final_metrics.leakage, baseline_.leakage - final_metrics.leakage,
      baseline_.area, final_metrics.area, baseline_.fanout_violations,
      final_metrics.fanout_violations, rejectSummary());
  markRunComplete(changed);
}

void RepairPowerPolicy::iterateMidAreaReclaim() {
  bool changed = false;
  Metrics current = collectTimingMetrics();
  while (committed_ < cfg_.max_moves && trials_ < cfg_.trial_limit) {
    std::vector<Candidate> candidates;
    for (const Target &target : collectMidAreaTargets()) {
      for (Candidate &candidate : generateMidAreaCandidates(target)) {
        candidate.score = scoreMidAreaCandidate(candidate);
        if (candidate.score > 0.0) {
          candidates.push_back(candidate);
        } else {
          ++rejected_;
          recordReject("mid_score");
        }
      }
      if (static_cast<int>(candidates.size()) >= cfg_.batch_size ||
          trials_ >= cfg_.trial_limit) {
        break;
      }
    }
    if (candidates.empty()) {
      break;
    }

    std::ranges::sort(candidates,
                      [](const Candidate &lhs, const Candidate &rhs) {
                        return lhs.score > rhs.score;
                      });

    int accepted_this_batch = 0;
    for (size_t i = 0; i < candidates.size();) {
      if (committed_ >= cfg_.max_moves || trials_ >= cfg_.trial_limit ||
          accepted_this_batch >= cfg_.batch_size) {
        break;
      }
      const Candidate &candidate = candidates[i];
      if (touched_.contains(candidate.target.inst)) {
        ++i;
        continue;
      }
      const int active_window_size = cfg_.adaptive_timing_window
                                         ? adaptive_window_size_
                                         : cfg_.timing_update_interval;
      if (active_window_size > 1 &&
          !candidateNeedsImmediateTimingGuard(candidate)) {
        std::vector<Candidate> window;
        std::unordered_set<sta::Instance *> window_insts;
        size_t j = i;
        while (j < candidates.size() &&
               static_cast<int>(window.size()) < active_window_size &&
               committed_ + accepted_this_batch +
                       static_cast<int>(window.size()) <
                   cfg_.max_moves &&
               trials_ < cfg_.trial_limit &&
               accepted_this_batch + static_cast<int>(window.size()) <
                   cfg_.batch_size) {
          const Candidate &window_candidate = candidates[j];
          if (touched_.contains(window_candidate.target.inst) ||
              window_insts.contains(window_candidate.target.inst)) {
            ++j;
            continue;
          }
          if (candidateNeedsImmediateTimingGuard(window_candidate)) {
            if (window.empty()) {
              break;
            }
            break;
          }
          ++trials_;
          window.push_back(window_candidate);
          window_insts.insert(window_candidate.target.inst);
          ++j;
        }
        if (window.empty()) {
          ++i;
          continue;
        }
        if (tryCommitGuardedSwapWindow(window, current)) {
          changed = true;
          committed_ += window.size();
          accepted_this_batch += window.size();
          noteAdaptiveWindowSuccess(window.size());
        } else {
          recordReject("mid_window_split_retry");
          noteAdaptiveWindowFailure("mid_window_split_retry");
          int split_rejects = 0;
          for (const Candidate &window_candidate : window) {
            if (committed_ >= cfg_.max_moves || trials_ >= cfg_.trial_limit ||
                accepted_this_batch >= cfg_.batch_size) {
              break;
            }
            if (tryCommitGuardedSwap(window_candidate, current)) {
              changed = true;
              ++committed_;
              ++accepted_this_batch;
            } else {
              ++rejected_;
              ++split_rejects;
            }
          }
          if (split_rejects * 2 >= static_cast<int>(window.size())) {
            noteAdaptiveWindowFailure("mid_split_retry_rejects");
          }
        }
        for (const Candidate &window_candidate : window) {
          touched_.insert(window_candidate.target.inst);
        }
        i = j;
        continue;
      }
      ++trials_;
      if (tryCommitGuardedSwap(candidate, current)) {
        changed = true;
        ++committed_;
        ++accepted_this_batch;
      } else {
        ++rejected_;
      }
      touched_.insert(candidate.target.inst);
      ++i;
    }
    if (accepted_this_batch == 0) {
      break;
    }
  }

  const Metrics final_metrics = collectMetrics();
  logger_->info(
      RSZ, 2332,
      "REPAIR_POWER|summary|phase={}|committed={}|trials={}|rejected={}|"
      "accepted_size_down={}|accepted_power_vt_swap={}|"
      "accepted_size_down_vt={}|accepted_remove_buffer={}|"
      "wns_before={}|wns_after={}|tns_before={}|tns_after={}|"
      "leakage_before={:.6g}|leakage_after={:.6g}|leakage_delta={:.6g}|"
      "area_before={:.6g}|area_after={:.6g}|fanout_violations_before={}|"
      "fanout_violations_after={}|reject_reasons={}",
      cfg_.phase, committed_, trials_, rejected_, accepted_size_down_,
      accepted_power_vt_swap_, accepted_size_down_vt_, accepted_remove_buffer_,
      sta::delayAsString(baseline_.wns, 3, sta_),
      sta::delayAsString(final_metrics.wns, 3, sta_),
      sta::delayAsString(baseline_.tns, 3, sta_),
      sta::delayAsString(final_metrics.tns, 3, sta_), baseline_.leakage,
      final_metrics.leakage, baseline_.leakage - final_metrics.leakage,
      baseline_.area, final_metrics.area, baseline_.fanout_violations,
      final_metrics.fanout_violations, rejectSummary());
  markRunComplete(changed);
}

void RepairPowerPolicy::iterateLateLeakageRecovery() {
  bool changed = false;
  Metrics current = collectMetrics();
  while (committed_ < cfg_.max_moves && trials_ < cfg_.trial_limit) {
    std::vector<Candidate> candidates;
    for (const Target &target : collectLateLeakageTargets()) {
      for (Candidate &candidate : generateLateLeakageCandidates(target)) {
        candidate.score = scoreLateLeakageCandidate(candidate);
        if (candidate.score > 0.0) {
          candidates.push_back(candidate);
        } else {
          ++rejected_;
          recordReject("late_score");
        }
      }
      if (static_cast<int>(candidates.size()) >= cfg_.batch_size ||
          trials_ >= cfg_.trial_limit) {
        break;
      }
    }
    if (candidates.empty()) {
      break;
    }

    std::ranges::sort(candidates,
                      [](const Candidate &lhs, const Candidate &rhs) {
                        return lhs.score > rhs.score;
                      });

    int accepted_this_batch = 0;
    for (size_t i = 0; i < candidates.size();) {
      if (committed_ >= cfg_.max_moves || trials_ >= cfg_.trial_limit ||
          accepted_this_batch >= cfg_.batch_size) {
        break;
      }
      const Candidate &candidate = candidates[i];
      if (touched_.contains(candidate.target.inst)) {
        ++i;
        continue;
      }
      const int active_window_size = cfg_.adaptive_timing_window
                                         ? adaptive_window_size_
                                         : cfg_.timing_update_interval;
      if (active_window_size > 1 &&
          !candidateNeedsImmediateTimingGuard(candidate)) {
        std::vector<Candidate> window;
        std::unordered_set<sta::Instance *> window_insts;
        size_t j = i;
        while (j < candidates.size() &&
               static_cast<int>(window.size()) < active_window_size &&
               committed_ + accepted_this_batch +
                       static_cast<int>(window.size()) <
                   cfg_.max_moves &&
               trials_ < cfg_.trial_limit &&
               accepted_this_batch + static_cast<int>(window.size()) <
                   cfg_.batch_size) {
          const Candidate &window_candidate = candidates[j];
          if (touched_.contains(window_candidate.target.inst) ||
              window_insts.contains(window_candidate.target.inst)) {
            ++j;
            continue;
          }
          if (candidateNeedsImmediateTimingGuard(window_candidate)) {
            if (window.empty()) {
              break;
            }
            break;
          }
          ++trials_;
          window.push_back(window_candidate);
          window_insts.insert(window_candidate.target.inst);
          ++j;
        }
        if (window.empty()) {
          ++i;
          continue;
        }
        if (tryCommitLateWindow(window, current)) {
          changed = true;
          committed_ += window.size();
          accepted_this_batch += window.size();
          noteAdaptiveWindowSuccess(window.size());
        } else {
          recordReject("late_window_split_retry");
          noteAdaptiveWindowFailure("late_window_split_retry");
          int split_rejects = 0;
          for (const Candidate &window_candidate : window) {
            if (committed_ >= cfg_.max_moves || trials_ >= cfg_.trial_limit ||
                accepted_this_batch >= cfg_.batch_size) {
              break;
            }
            if (tryCommitLateCandidate(window_candidate, current)) {
              changed = true;
              ++committed_;
              ++accepted_this_batch;
            } else {
              ++rejected_;
              ++split_rejects;
            }
          }
          if (split_rejects * 2 >= static_cast<int>(window.size())) {
            noteAdaptiveWindowFailure("late_split_retry_rejects");
          }
        }
        for (const Candidate &window_candidate : window) {
          touched_.insert(window_candidate.target.inst);
        }
        i = j;
        continue;
      }
      ++trials_;
      if (tryCommitLateCandidate(candidate, current)) {
        changed = true;
        ++committed_;
        ++accepted_this_batch;
      } else {
        ++rejected_;
      }
      touched_.insert(candidate.target.inst);
      ++i;
    }
    if (accepted_this_batch == 0) {
      break;
    }
  }

  const Metrics final_metrics = collectMetrics();
  logger_->info(
      RSZ, 2333,
      "REPAIR_POWER|summary|phase={}|committed={}|trials={}|rejected={}|"
      "accepted_size_down={}|accepted_power_vt_swap={}|"
      "accepted_size_down_vt={}|accepted_remove_buffer={}|"
      "wns_before={}|wns_after={}|tns_before={}|tns_after={}|"
      "leakage_before={:.6g}|leakage_after={:.6g}|leakage_delta={:.6g}|"
      "area_before={:.6g}|area_after={:.6g}|fanout_violations_before={}|"
      "fanout_violations_after={}|reject_reasons={}",
      cfg_.phase, committed_, trials_, rejected_, accepted_size_down_,
      accepted_power_vt_swap_, accepted_size_down_vt_, accepted_remove_buffer_,
      sta::delayAsString(baseline_.wns, 3, sta_),
      sta::delayAsString(final_metrics.wns, 3, sta_),
      sta::delayAsString(baseline_.tns, 3, sta_),
      sta::delayAsString(final_metrics.tns, 3, sta_), baseline_.leakage,
      final_metrics.leakage, baseline_.leakage - final_metrics.leakage,
      baseline_.area, final_metrics.area, baseline_.fanout_violations,
      final_metrics.fanout_violations, rejectSummary());
  markRunComplete(changed);
}

std::vector<RepairPowerPolicy::Target> RepairPowerPolicy::collectTargets() {
  std::vector<Target> targets;
  targets.reserve(std::min(12000, cfg_.max_targets));
  sta::LeafInstanceIterator *iter = network_->leafInstanceIterator();
  while (iter->hasNext()) {
    sta::Instance *inst = iter->next();
    if (touched_.contains(inst) || !isContestModifiableInstance(inst)) {
      continue;
    }
    sta::LibertyCell *cell = network_->libertyCell(inst);
    sta::Pin *output_pin = findOutputPin(inst);
    if (output_pin == nullptr || sta_->isClock(output_pin, sta_->cmdMode())) {
      continue;
    }

    Target target;
    target.inst = inst;
    target.output_pin = output_pin;
    target.cell = cell;
    target.slack = worstOutputSlack(inst);
    const bool is_buffer = cell->isBuffer();
    const double slack_floor =
        is_buffer ? cfg_.min_unbuffer_slack : cfg_.min_target_slack;
    if (target.slack < slack_floor) {
      continue;
    }
    target.fanout = outputFanout(output_pin);
    target.leakage = cellLeakage(cell);
    target.area = cellArea(cell);
    target.input_cap = inputCapProxy(cell);
    const double slack_bonus = std::max(0.0, target.slack) * 10.0;
    const double fanout_penalty = std::max(0, target.fanout - 8) * 0.02;
    target.priority =
        cfg_.leakage_weight * target.leakage + cfg_.area_weight * target.area +
        cfg_.input_cap_weight * target.input_cap + slack_bonus - fanout_penalty;
    if (!cell->hasSequentials() && is_buffer &&
        accepted_remove_buffer_ < cfg_.max_unbuffer_moves &&
        cfg_.max_unbuffer_fanout > 0 &&
        target.fanout <= cfg_.max_unbuffer_fanout &&
        passesBufferRemovalFanoutGuard(target) &&
        resizer_.canRemoveBuffer(inst, true)) {
      target.priority +=
          cfg_.buffer_bonus + cfg_.leakage_weight * target.leakage;
    }
    targets.push_back(target);
  }
  delete iter;

  std::ranges::sort(targets, [](const Target &lhs, const Target &rhs) {
    return lhs.priority > rhs.priority;
  });
  if (targets.size() > static_cast<size_t>(cfg_.max_targets)) {
    targets.resize(cfg_.max_targets);
  }
  return targets;
}

std::vector<RepairPowerPolicy::Target>
RepairPowerPolicy::collectMidAreaTargets() {
  std::vector<Target> targets;
  targets.reserve(std::min(12000, cfg_.max_targets));
  sta::LeafInstanceIterator *iter = network_->leafInstanceIterator();
  while (iter->hasNext()) {
    sta::Instance *inst = iter->next();
    if (touched_.contains(inst) || !isContestModifiableInstance(inst)) {
      continue;
    }
    sta::LibertyCell *cell = network_->libertyCell(inst);
    sta::Pin *output_pin = findOutputPin(inst);
    if (output_pin == nullptr || sta_->isClock(output_pin, sta_->cmdMode())) {
      continue;
    }

    Target target;
    target.inst = inst;
    target.output_pin = output_pin;
    target.cell = cell;
    target.slack = worstOutputSlack(inst);
    target.fanout = outputFanout(output_pin);
    target.leakage = cellLeakage(cell);
    target.area = cellArea(cell);
    target.input_cap = inputCapProxy(cell);
    const double slack_bonus = slackBonus(target.slack, 6.0);
    const double fanout_penalty = std::max(0, target.fanout - 10) * 0.04;
    target.priority = cfg_.leakage_weight * target.leakage +
                      cfg_.input_cap_weight * target.input_cap + slack_bonus -
                      fanout_penalty;
    targets.push_back(target);
  }
  delete iter;

  std::ranges::sort(targets, [](const Target &lhs, const Target &rhs) {
    return lhs.priority > rhs.priority;
  });
  if (targets.size() > static_cast<size_t>(cfg_.max_targets)) {
    targets.resize(cfg_.max_targets);
  }
  return targets;
}

std::vector<RepairPowerPolicy::Target>
RepairPowerPolicy::collectLateLeakageTargets() {
  std::vector<Target> targets;
  targets.reserve(std::min(15000, cfg_.max_targets));
  sta::LeafInstanceIterator *iter = network_->leafInstanceIterator();
  while (iter->hasNext()) {
    sta::Instance *inst = iter->next();
    if (touched_.contains(inst) || !isContestModifiableInstance(inst)) {
      continue;
    }
    sta::LibertyCell *cell = network_->libertyCell(inst);
    sta::Pin *output_pin = findOutputPin(inst);
    if (output_pin == nullptr || sta_->isClock(output_pin, sta_->cmdMode())) {
      continue;
    }

    Target target;
    target.inst = inst;
    target.output_pin = output_pin;
    target.cell = cell;
    target.slack = worstOutputSlack(inst);
    if (target.slack < cfg_.min_target_slack) {
      continue;
    }
    target.fanout = outputFanout(output_pin);
    target.leakage = cellLeakage(cell);
    target.area = cellArea(cell);
    target.input_cap = inputCapProxy(cell);
    const int vt_rank = powerVtRank(cell);
    const bool post_route_sensitive_late =
        std::abs(static_cast<double>(baseline_.tns)) < 20.0e-9;
    const bool vt_reclaimable = vt_rank >= 0 && vt_rank < 2;
    const bool protected_timing_cone =
        post_route_sensitive_late && target.slack < cfg_.late_protected_slack &&
        target.fanout > cfg_.late_protected_fanout;
    const bool slack_ok_for_vt =
        target.slack >= cfg_.late_vt_min_target_slack ||
        (!protected_timing_cone && target.fanout <= 4);
    if (protected_timing_cone) {
      recordReject("late_protected_cone_target");
      continue;
    }
    if ((!vt_reclaimable || !slack_ok_for_vt) &&
        target.slack < cfg_.min_target_slack) {
      continue;
    }
    const bool safe_size_reclaimable =
        !post_route_sensitive_late && target.slack >= cfg_.min_unbuffer_slack;
    const bool safe_buffer_reclaimable =
        !post_route_sensitive_late && cell->isBuffer() &&
        accepted_remove_buffer_ < cfg_.max_unbuffer_moves &&
        cfg_.max_unbuffer_fanout > 0 &&
        target.fanout <= cfg_.max_unbuffer_fanout &&
        passesBufferRemovalFanoutGuard(target) &&
        resizer_.canRemoveBuffer(inst, true);
    if (!vt_reclaimable && !safe_size_reclaimable && !safe_buffer_reclaimable) {
      continue;
    }
    const double rank_bonus = vt_rank == 0 ? 0.20 : vt_rank == 1 ? 0.05 : 0.0;
    const double slack_bonus = slackBonus(target.slack, 10.0);
    const double fanout_penalty = std::max(0, target.fanout - 4) * 0.06;
    const double recovered_path_penalty =
        post_route_sensitive_late && target.slack < cfg_.late_protected_slack
            ? 0.15
            : 0.0;
    target.priority = cfg_.leakage_weight * target.leakage + rank_bonus +
                      cfg_.area_weight * target.area +
                      cfg_.input_cap_weight * target.input_cap + slack_bonus -
                      fanout_penalty - recovered_path_penalty;
    if (safe_buffer_reclaimable) {
      target.priority +=
          cfg_.buffer_bonus + cfg_.leakage_weight * target.leakage;
    }
    targets.push_back(target);
  }
  delete iter;

  std::ranges::sort(targets, [](const Target &lhs, const Target &rhs) {
    return lhs.priority > rhs.priority;
  });
  if (targets.size() > static_cast<size_t>(cfg_.max_targets)) {
    targets.resize(cfg_.max_targets);
  }
  return targets;
}

std::vector<RepairPowerPolicy::Candidate>
RepairPowerPolicy::generateCandidates(const Target &target) {
  std::vector<Candidate> candidates;
  std::unordered_set<sta::LibertyCell *> seen;

  if (accepted_remove_buffer_ < cfg_.max_unbuffer_moves &&
      cfg_.max_unbuffer_fanout > 0 && target.cell->isBuffer() &&
      target.fanout <= cfg_.max_unbuffer_fanout &&
      passesBufferRemovalFanoutGuard(target) &&
      resizer_.canRemoveBuffer(target.inst, true)) {
    Candidate candidate;
    candidate.target = target;
    candidate.kind = MoveKind::kRemoveBuffer;
    candidate.leakage_gain = target.leakage;
    candidate.area_gain = target.area;
    candidate.input_cap_gain = target.input_cap;
    candidates.push_back(candidate);
  }

  auto add_candidate = [&](sta::LibertyCell *replacement) {
    if (replacement == nullptr || replacement == target.cell ||
        seen.contains(replacement) || resizer_.dontUse(replacement) ||
        !footprintOk(target.cell, replacement)) {
      return;
    }
    const MoveKind kind = classifyMove(target.cell, replacement);
    if (!usefulCandidate(target.cell, replacement, kind)) {
      return;
    }
    Candidate candidate;
    candidate.target = target;
    candidate.replacement = replacement;
    candidate.kind = kind;
    candidate.leakage_gain =
        cellLeakage(target.cell) - cellLeakage(replacement);
    candidate.area_gain = cellArea(target.cell) - cellArea(replacement);
    candidate.input_cap_gain =
        inputCapProxy(target.cell) - inputCapProxy(replacement);
    seen.insert(replacement);
    candidates.push_back(candidate);
  };

  if (cfg_.enable_vt_swap) {
    for (sta::LibertyCell *cell : resizer_.getVTEquivCells(target.cell)) {
      add_candidate(cell);
      if (candidates.size() >= 6) {
        break;
      }
    }
  }

  std::ranges::sort(candidates, [this](const Candidate &lhs,
                                       const Candidate &rhs) {
    return scoreCandidate(lhs) > scoreCandidate(rhs);
  });

  const size_t vt_candidate_count = candidates.size();
  for (sta::LibertyCell *cell : resizer_.getSwappableCells(target.cell)) {
    add_candidate(cell);
    if (candidates.size() >= vt_candidate_count + 18 ||
        candidates.size() >= 24) {
      break;
    }
  }
  for (Candidate &candidate : candidates) {
    candidate.score = scoreCandidate(candidate);
  }
  std::ranges::sort(candidates, [this](const Candidate &lhs,
                                       const Candidate &rhs) {
    return stabilitySampledPreferred(lhs, rhs);
  });
  markStabilitySampledTies(candidates);
  if (candidates.size() > 12) {
    candidates.resize(12);
  }
  return candidates;
}

std::vector<RepairPowerPolicy::Candidate>
RepairPowerPolicy::generateMidAreaCandidates(const Target &target) {
  std::vector<Candidate> candidates;
  std::unordered_set<sta::LibertyCell *> seen;

  for (sta::LibertyCell *cell : resizer_.getVTEquivCells(target.cell)) {
    if (cell == nullptr || cell == target.cell || seen.contains(cell) ||
        resizer_.dontUse(cell) || !footprintOk(target.cell, cell) ||
        !isPowerDirectionVtSwap(target.cell, cell)) {
      continue;
    }
    const MoveKind kind = classifyMove(target.cell, cell);
    if (kind != MoveKind::kPowerVtSwap) {
      continue;
    }
    Candidate candidate;
    candidate.target = target;
    candidate.replacement = cell;
    candidate.kind = kind;
    candidate.leakage_gain = cellLeakage(target.cell) - cellLeakage(cell);
    candidate.area_gain = cellArea(target.cell) - cellArea(cell);
    candidate.input_cap_gain = inputCapProxy(target.cell) - inputCapProxy(cell);
    if (candidate.leakage_gain <= 0.0 && candidate.area_gain <= 0.0 &&
        candidate.input_cap_gain <= 0.0) {
      continue;
    }
    seen.insert(cell);
    candidates.push_back(candidate);
  }
  return candidates;
}

std::vector<RepairPowerPolicy::Candidate>
RepairPowerPolicy::generateLateLeakageCandidates(const Target &target) {
  std::vector<Candidate> candidates;
  std::unordered_set<sta::LibertyCell *> seen;
  const bool post_route_sensitive_late =
      std::abs(static_cast<double>(baseline_.tns)) < 20.0e-9;
  const bool protected_timing_cone = post_route_sensitive_late &&
                                     target.slack < cfg_.late_protected_slack &&
                                     target.fanout > cfg_.late_protected_fanout;

  if (!post_route_sensitive_late &&
      accepted_remove_buffer_ < cfg_.max_unbuffer_moves &&
      cfg_.max_unbuffer_fanout > 0 && target.cell->isBuffer() &&
      target.slack >= cfg_.min_unbuffer_slack &&
      target.fanout <= cfg_.max_unbuffer_fanout &&
      passesBufferRemovalFanoutGuard(target) &&
      resizer_.canRemoveBuffer(target.inst, true)) {
    Candidate candidate;
    candidate.target = target;
    candidate.kind = MoveKind::kRemoveBuffer;
    candidate.leakage_gain = target.leakage;
    candidate.area_gain = target.area;
    candidate.input_cap_gain = target.input_cap;
    candidates.push_back(candidate);
  }

  for (sta::LibertyCell *cell : resizer_.getVTEquivCells(target.cell)) {
    if (cell == nullptr || cell == target.cell || seen.contains(cell) ||
        resizer_.dontUse(cell) || !footprintOk(target.cell, cell) ||
        !isStrictPowerDirectionVtSwap(target.cell, cell)) {
      continue;
    }
    const MoveKind kind = classifyMove(target.cell, cell);
    if (kind != MoveKind::kPowerVtSwap) {
      continue;
    }
    Candidate candidate;
    candidate.target = target;
    candidate.replacement = cell;
    candidate.kind = kind;
    candidate.leakage_gain = cellLeakage(target.cell) - cellLeakage(cell);
    candidate.area_gain = cellArea(target.cell) - cellArea(cell);
    candidate.input_cap_gain = inputCapProxy(target.cell) - inputCapProxy(cell);
    if (candidate.leakage_gain <= 0.0) {
      continue;
    }
    if (protected_timing_cone &&
        candidate.leakage_gain < target.leakage * 0.12) {
      recordReject("late_low_gain_protected_vt");
      continue;
    }
    seen.insert(cell);
    candidates.push_back(candidate);
  }

  if (post_route_sensitive_late || target.slack < cfg_.min_unbuffer_slack) {
    return candidates;
  }

  for (sta::LibertyCell *cell : resizer_.getSwappableCells(target.cell)) {
    if (cell == nullptr || cell == target.cell || seen.contains(cell) ||
        resizer_.dontUse(cell) || !footprintOk(target.cell, cell)) {
      continue;
    }
    const MoveKind kind = classifyMove(target.cell, cell);
    if (kind != MoveKind::kSizeDown &&
        kind != MoveKind::kSizeDownAndPowerVtSwap) {
      continue;
    }
    if (!usefulCandidate(target.cell, cell, kind)) {
      continue;
    }
    Candidate candidate;
    candidate.target = target;
    candidate.replacement = cell;
    candidate.kind = kind;
    candidate.leakage_gain = cellLeakage(target.cell) - cellLeakage(cell);
    candidate.area_gain = cellArea(target.cell) - cellArea(cell);
    candidate.input_cap_gain = inputCapProxy(target.cell) - inputCapProxy(cell);
    if (candidate.leakage_gain <= 0.0 && candidate.area_gain <= 0.0 &&
        candidate.input_cap_gain <= 0.0) {
      continue;
    }
    seen.insert(cell);
    candidates.push_back(candidate);
    if (candidates.size() >= 6) {
      break;
    }
  }
  return candidates;
}

bool RepairPowerPolicy::tryCommitCandidate(const Candidate &candidate,
                                           Metrics &current) {
  if (!isContestModifiableInstance(candidate.target.inst)) {
    recordReject("contest_guard");
    return false;
  }
  if (candidate.kind == MoveKind::kSizeDownAndPowerVtSwap) {
    if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                             candidate.replacement)) {
      recordReject("max_cap");
      return false;
    }
    if (!withinCompoundVtQuota(candidate)) {
      recordReject("compound_vt_quota");
      return false;
    }
    if (excludeCompoundVtCriticalHalo(candidate)) {
      recordReject("compound_vt_critical_halo");
      return false;
    }
    if (excludeCompoundVtCriticalFaninHalo(candidate)) {
      recordReject("compound_vt_critical_fanin_halo");
      return false;
    }
  }
  const std::string inst_name = instName(candidate.target.inst);
  const std::vector<ElectricalState> electrical_before =
      captureElectricalState({candidate});
  resizer_.journalBegin();
  bool applied = false;
  if (candidate.kind == MoveKind::kRemoveBuffer) {
    if (accepted_remove_buffer_ >= cfg_.max_unbuffer_moves) {
      resizer_.journalRestore();
      recordReject("unbuffer_quota");
      return false;
    }
    if (!resizer_.canRemoveBuffer(candidate.target.inst, true) ||
        !passesBufferRemovalFanoutGuard(candidate.target)) {
      resizer_.journalRestore();
      recordReject("unbuffer_precheck");
      return false;
    }
    applied = resizer_.removeBuffer(candidate.target.inst);
  } else {
    if (candidate.kind == MoveKind::kPowerVtSwap &&
        accepted_power_vt_swap_ >= cfg_.max_power_vt_swaps) {
      resizer_.journalRestore();
      recordReject("vt_quota");
      return false;
    }
    if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                             candidate.replacement)) {
      resizer_.journalRestore();
      recordReject("max_cap");
      return false;
    }
    if (excludePureVtCriticalHalo(candidate)) {
      resizer_.journalRestore();
      recordReject("pure_vt_critical_halo");
      return false;
    }
    applied =
        resizer_.replaceCell(candidate.target.inst, candidate.replacement);
  }
  if (!applied) {
    resizer_.journalRestore();
    recordReject("apply");
    return false;
  }

  resizer_.updateParasiticsAndTiming();
  if (introducesElectricalViolation(electrical_before)) {
    resizer_.journalRestore();
    recordReject("electrical_guard");
    logger_->report("METRIC|repair_power_electrical_rejected|1");
    return false;
  }
  Metrics after = collectTimingMetrics();
  if (!withinPhaseBudget(current, after, baseline_)) {
    resizer_.journalRestore();
    recordReject("phase_budget");
    return false;
  }
  if (cfg_.full_metrics_interval <= 1 ||
      ((committed_ + 1) % cfg_.full_metrics_interval) == 0) {
    after = collectMetrics();
    if (!withinPhaseBudget(current, after, baseline_)) {
      resizer_.journalRestore();
      recordReject("full_phase_budget");
      return false;
    }
  }

  resizer_.journalEndNoTimingUpdate();
  logger_->report("METRIC|repair_power_electrical_retained|1");
  reportPersistentRetained(1);
  current = after;
  accountAcceptedCandidate(candidate);
  logger_->info(RSZ, 2322,
                "REPAIR_POWER|accept|phase={}|inst={}|kind={}|from={}|to={}|"
                "score={:.6g}|leak_gain={:.6g}|area_gain={:.6g}|"
                "cap_gain={:.6g}|wns={}|tns={}",
                cfg_.phase, inst_name, moveKindName(candidate.kind),
                candidate.target.cell != nullptr ? candidate.target.cell->name()
                                                 : "<null>",
                candidate.replacement != nullptr ? candidate.replacement->name()
                                                 : "<deleted>",
                candidate.score, candidate.leakage_gain, candidate.area_gain,
                candidate.input_cap_gain,
                sta::delayAsString(after.wns, 3, sta_),
                sta::delayAsString(after.tns, 3, sta_));
  return true;
}

bool RepairPowerPolicy::tryCommitCandidateWindow(
    const std::vector<Candidate> &window, Metrics &current) {
  if (window.empty()) {
    return false;
  }
  for (const Candidate &candidate : window) {
    if (!isContestModifiableInstance(candidate.target.inst)) {
      recordReject("window_contest_guard");
      return false;
    }
  }

  const std::vector<ElectricalState> electrical_before =
      captureElectricalState(window);
  int pure_vt_swaps = 0;
  int compound_vt_swaps = 0;
  for (const Candidate &candidate : window) {
    if (candidateNeedsImmediateTimingGuard(candidate)) {
      recordReject("window_immediate_guard");
      return false;
    }
    if (candidate.kind == MoveKind::kPowerVtSwap) {
      ++pure_vt_swaps;
    }
    if (accepted_power_vt_swap_ + pure_vt_swaps >
        cfg_.max_power_vt_swaps) {
      recordReject("window_vt_quota");
      return false;
    }
    if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                             candidate.replacement)) {
      recordReject("window_max_cap");
      return false;
    }
    if (excludePureVtCriticalHalo(candidate)) {
      recordReject("window_pure_vt_critical_halo");
      return false;
    }
    if (candidate.kind == MoveKind::kSizeDownAndPowerVtSwap) {
      ++compound_vt_swaps;
      if (!withinCompoundVtQuota(candidate, compound_vt_swaps)) {
        recordReject("window_compound_vt_quota");
        return false;
      }
      if (excludeCompoundVtCriticalHalo(candidate)) {
        recordReject("window_compound_vt_critical_halo");
        return false;
      }
      if (excludeCompoundVtCriticalFaninHalo(candidate)) {
        recordReject("window_compound_vt_critical_fanin_halo");
        return false;
      }
    }
  }

  resizer_.journalBegin();
  double leakage_gain = 0.0;
  double area_gain = 0.0;
  double input_cap_gain = 0.0;
  for (const Candidate &candidate : window) {
    if (!resizer_.replaceCell(candidate.target.inst, candidate.replacement)) {
      resizer_.journalRestore();
      recordReject("window_apply");
      return false;
    }
    leakage_gain += candidate.leakage_gain;
    area_gain += candidate.area_gain;
    input_cap_gain += candidate.input_cap_gain;
  }

  resizer_.updateParasiticsAndTiming();
  if (introducesElectricalViolation(electrical_before)) {
    resizer_.journalRestore();
    recordReject("window_electrical_guard");
    logger_->report("METRIC|repair_power_electrical_rejected|{}",
                    window.size());
    return false;
  }
  Metrics after = collectTimingMetrics();
  if (!withinPhaseBudget(current, after, baseline_)) {
    resizer_.journalRestore();
    recordReject("window_phase_budget");
    return false;
  }
  const int next_committed = committed_ + static_cast<int>(window.size());
  if (cfg_.full_metrics_interval <= 1 ||
      (next_committed % cfg_.full_metrics_interval) <
          static_cast<int>(window.size())) {
    after = collectMetrics();
    if (!withinPhaseBudget(current, after, baseline_)) {
      resizer_.journalRestore();
      recordReject("window_full_phase_budget");
      return false;
    }
  }

  resizer_.journalEndNoTimingUpdate();
  logger_->report("METRIC|repair_power_electrical_retained|{}",
                  window.size());
  reportPersistentRetained(window.size());
  current = after;
  for (const Candidate &candidate : window) {
    accountAcceptedCandidate(candidate);
  }
  logger_->info(RSZ, 2329,
                "REPAIR_POWER|accept_window|phase={}|count={}|"
                "leak_gain={:.6g}|area_gain={:.6g}|cap_gain={:.6g}|"
                "wns={}|tns={}",
                cfg_.phase, window.size(), leakage_gain, area_gain,
                input_cap_gain, sta::delayAsString(after.wns, 3, sta_),
                sta::delayAsString(after.tns, 3, sta_));
  return true;
}

bool RepairPowerPolicy::tryCommitGuardedSwap(const Candidate &candidate,
                                             Metrics &current) {
  if (!isContestModifiableInstance(candidate.target.inst)) {
    recordReject("contest_guard");
    return false;
  }
  if (candidate.kind != MoveKind::kPowerVtSwap ||
      candidate.replacement == nullptr) {
    recordReject("phase_kind");
    return false;
  }
  if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                           candidate.replacement)) {
    recordReject("max_cap");
    return false;
  }

  const std::string inst_name = instName(candidate.target.inst);
  const std::vector<ElectricalState> electrical_before =
      captureElectricalState({candidate});
  resizer_.journalBegin();
  if (!resizer_.replaceCell(candidate.target.inst, candidate.replacement)) {
    resizer_.journalRestore();
    recordReject("apply");
    return false;
  }

  resizer_.updateParasiticsAndTiming();
  if (introducesElectricalViolation(electrical_before)) {
    resizer_.journalRestore();
    recordReject("electrical_guard");
    logger_->report("METRIC|repair_power_electrical_rejected|1");
    return false;
  }
  Metrics after = collectTimingMetrics();
  const bool within_budget =
      cfg_.phase == "late_leakage_recovery"
          ? withinLateLeakageBudget(current, after, baseline_)
          : withinMidAreaBudget(current, after, baseline_);
  if (!within_budget) {
    resizer_.journalRestore();
    recordReject("phase_budget");
    return false;
  }
  if (cfg_.full_metrics_interval <= 1 ||
      ((committed_ + 1) % cfg_.full_metrics_interval) == 0) {
    after = collectMetrics();
    const bool within_full_budget =
        cfg_.phase == "late_leakage_recovery"
            ? withinLateLeakageBudget(current, after, baseline_)
            : withinMidAreaBudget(current, after, baseline_);
    if (!within_full_budget) {
      resizer_.journalRestore();
      recordReject("full_phase_budget");
      return false;
    }
  }

  resizer_.journalEndNoTimingUpdate();
  logger_->report("METRIC|repair_power_electrical_retained|1");
  current = after;
  accountAcceptedCandidate(candidate);
  logger_->info(RSZ, 2334,
                "REPAIR_POWER|accept|phase={}|inst={}|kind={}|from={}|to={}|"
                "score={:.6g}|leak_gain={:.6g}|area_gain={:.6g}|"
                "cap_gain={:.6g}|slack_before={}|wns={}|tns={}",
                cfg_.phase, inst_name, moveKindName(candidate.kind),
                candidate.target.cell != nullptr ? candidate.target.cell->name()
                                                 : "<null>",
                candidate.replacement != nullptr ? candidate.replacement->name()
                                                 : "<deleted>",
                candidate.score, candidate.leakage_gain, candidate.area_gain,
                candidate.input_cap_gain,
                sta::delayAsString(candidate.target.slack, 3, sta_),
                sta::delayAsString(after.wns, 3, sta_),
                sta::delayAsString(after.tns, 3, sta_));
  return true;
}

bool RepairPowerPolicy::tryCommitGuardedSwapWindow(
    const std::vector<Candidate> &window, Metrics &current) {
  if (window.empty()) {
    return false;
  }
  for (const Candidate &candidate : window) {
    if (!isContestModifiableInstance(candidate.target.inst)) {
      recordReject("window_contest_guard");
      return false;
    }
  }

  const std::vector<ElectricalState> electrical_before =
      captureElectricalState(window);
  resizer_.journalBegin();
  double leakage_gain = 0.0;
  double area_gain = 0.0;
  double input_cap_gain = 0.0;
  for (const Candidate &candidate : window) {
    if (candidate.kind != MoveKind::kPowerVtSwap ||
        candidate.replacement == nullptr) {
      resizer_.journalRestore();
      recordReject("window_phase_kind");
      return false;
    }
    if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                             candidate.replacement)) {
      resizer_.journalRestore();
      recordReject("window_max_cap");
      return false;
    }
    if (!resizer_.replaceCell(candidate.target.inst, candidate.replacement)) {
      resizer_.journalRestore();
      recordReject("window_apply");
      return false;
    }
    leakage_gain += candidate.leakage_gain;
    area_gain += candidate.area_gain;
    input_cap_gain += candidate.input_cap_gain;
  }

  resizer_.updateParasiticsAndTiming();
  if (introducesElectricalViolation(electrical_before)) {
    resizer_.journalRestore();
    recordReject("window_electrical_guard");
    logger_->report("METRIC|repair_power_electrical_rejected|{}",
                    window.size());
    return false;
  }
  Metrics after = collectTimingMetrics();
  const bool within_budget =
      cfg_.phase == "late_leakage_recovery"
          ? withinLateLeakageBudget(current, after, baseline_)
          : withinMidAreaBudget(current, after, baseline_);
  if (!within_budget) {
    resizer_.journalRestore();
    recordReject("window_phase_budget");
    return false;
  }

  const int next_committed = committed_ + static_cast<int>(window.size());
  if (cfg_.full_metrics_interval <= 1 ||
      (next_committed % cfg_.full_metrics_interval) <
          static_cast<int>(window.size())) {
    after = collectMetrics();
    const bool within_full_budget =
        cfg_.phase == "late_leakage_recovery"
            ? withinLateLeakageBudget(current, after, baseline_)
            : withinMidAreaBudget(current, after, baseline_);
    if (!within_full_budget) {
      resizer_.journalRestore();
      recordReject("window_full_phase_budget");
      return false;
    }
  }

  resizer_.journalEndNoTimingUpdate();
  logger_->report("METRIC|repair_power_electrical_retained|{}",
                  window.size());
  current = after;
  for (const Candidate &candidate : window) {
    accountAcceptedCandidate(candidate);
  }
  logger_->info(RSZ, 2335,
                "REPAIR_POWER|accept_guarded_window|phase={}|count={}|"
                "leak_gain={:.6g}|area_gain={:.6g}|cap_gain={:.6g}|"
                "window_size={}|wns={}|tns={}",
                cfg_.phase, window.size(), leakage_gain, area_gain,
                input_cap_gain, adaptive_window_size_,
                sta::delayAsString(after.wns, 3, sta_),
                sta::delayAsString(after.tns, 3, sta_));
  return true;
}

bool RepairPowerPolicy::tryCommitLateCandidate(const Candidate &candidate,
                                               Metrics &current) {
  if (!isContestModifiableInstance(candidate.target.inst)) {
    recordReject("late_contest_guard");
    return false;
  }
  const std::string inst_name = instName(candidate.target.inst);
  const std::vector<ElectricalState> electrical_before =
      captureElectricalState({candidate});
  resizer_.journalBegin();
  bool applied = false;
  if (candidate.kind == MoveKind::kRemoveBuffer) {
    if (accepted_remove_buffer_ >= cfg_.max_unbuffer_moves) {
      resizer_.journalRestore();
      recordReject("late_unbuffer_quota");
      return false;
    }
    if (!resizer_.canRemoveBuffer(candidate.target.inst, true) ||
        !passesBufferRemovalFanoutGuard(candidate.target)) {
      resizer_.journalRestore();
      recordReject("late_unbuffer_precheck");
      return false;
    }
    applied = resizer_.removeBuffer(candidate.target.inst);
  } else {
    if (candidate.replacement == nullptr) {
      resizer_.journalRestore();
      recordReject("late_null_replacement");
      return false;
    }
    if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                             candidate.replacement)) {
      resizer_.journalRestore();
      recordReject("late_max_cap");
      return false;
    }
    applied =
        resizer_.replaceCell(candidate.target.inst, candidate.replacement);
  }
  if (!applied) {
    resizer_.journalRestore();
    recordReject("late_apply");
    return false;
  }

  resizer_.updateParasiticsAndTiming();
  if (introducesElectricalViolation(electrical_before)) {
    resizer_.journalRestore();
    recordReject("late_electrical_guard");
    logger_->report("METRIC|repair_power_electrical_rejected|1");
    return false;
  }
  Metrics after = collectMetrics();
  if (!withinLateTimingBudget(current, after, baseline_)) {
    resizer_.journalRestore();
    if (cfg_.authoritative_timing_budget) {
      logger_->report("METRIC|repair_power_timing_rejected|1");
    }
    recordReject("late_timing_budget");
    return false;
  }
  if (!withinLateWeightedPowerTimingBudget(current, after, baseline_)) {
    resizer_.journalRestore();
    recordReject("late_weighted_budget");
    return false;
  }
  const double retained_gain = current.leakage - after.leakage;
  if (retained_gain <= 0.0) {
    resizer_.journalRestore();
    recordReject("late_measured_leakage");
    return false;
  }

  resizer_.journalEndNoTimingUpdate();
  logger_->report("METRIC|repair_power_electrical_retained|1");
  current = after;
  accountAcceptedCandidate(candidate);
  logger_->report("METRIC|repair_power_guarded_moves|1");
  logger_->report("METRIC|repair_power_retained_gain|{:.6g}", retained_gain);
  if (cfg_.authoritative_timing_budget) {
    logger_->report("METRIC|repair_power_timing_retained|1");
  }
  logger_->info(RSZ, 2336,
                "REPAIR_POWER|accept_late|phase={}|inst={}|kind={}|from={}|"
                "to={}|score={:.6g}|leak_gain={:.6g}|area_gain={:.6g}|"
                "cap_gain={:.6g}|slack_before={}|wns={}|tns={}",
                cfg_.phase, inst_name, moveKindName(candidate.kind),
                candidate.target.cell != nullptr ? candidate.target.cell->name()
                                                 : "<null>",
                candidate.replacement != nullptr ? candidate.replacement->name()
                                                 : "<deleted>",
                candidate.score, candidate.leakage_gain, candidate.area_gain,
                candidate.input_cap_gain,
                sta::delayAsString(candidate.target.slack, 3, sta_),
                sta::delayAsString(after.wns, 3, sta_),
                sta::delayAsString(after.tns, 3, sta_));
  return true;
}

bool RepairPowerPolicy::tryCommitLateWindow(
    const std::vector<Candidate> &window, Metrics &current) {
  if (window.empty()) {
    return false;
  }
  for (const Candidate &candidate : window) {
    if (!isContestModifiableInstance(candidate.target.inst)) {
      recordReject("late_window_contest_guard");
      return false;
    }
  }

  const std::vector<ElectricalState> electrical_before =
      captureElectricalState(window);
  resizer_.journalBegin();
  double leakage_gain = 0.0;
  double area_gain = 0.0;
  double input_cap_gain = 0.0;
  for (const Candidate &candidate : window) {
    if (candidate.kind == MoveKind::kRemoveBuffer ||
        candidate.replacement == nullptr) {
      resizer_.journalRestore();
      recordReject("late_window_phase_kind");
      return false;
    }
    if (!resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                             candidate.replacement)) {
      resizer_.journalRestore();
      recordReject("late_window_max_cap");
      return false;
    }
    if (!resizer_.replaceCell(candidate.target.inst, candidate.replacement)) {
      resizer_.journalRestore();
      recordReject("late_window_apply");
      return false;
    }
    leakage_gain += candidate.leakage_gain;
    area_gain += candidate.area_gain;
    input_cap_gain += candidate.input_cap_gain;
  }

  resizer_.updateParasiticsAndTiming();
  if (introducesElectricalViolation(electrical_before)) {
    resizer_.journalRestore();
    recordReject("late_window_electrical_guard");
    logger_->report("METRIC|repair_power_electrical_rejected|{}",
                    window.size());
    return false;
  }
  Metrics after = collectMetrics();
  if (!withinLateTimingBudget(current, after, baseline_)) {
    resizer_.journalRestore();
    if (cfg_.authoritative_timing_budget) {
      logger_->report("METRIC|repair_power_timing_rejected|{}", window.size());
    }
    recordReject("late_window_timing_budget");
    return false;
  }
  if (!withinLateWeightedPowerTimingBudget(current, after, baseline_)) {
    resizer_.journalRestore();
    recordReject("late_window_weighted_budget");
    return false;
  }
  const double retained_gain = current.leakage - after.leakage;
  if (retained_gain <= 0.0) {
    resizer_.journalRestore();
    recordReject("late_window_measured_leakage");
    return false;
  }

  resizer_.journalEndNoTimingUpdate();
  logger_->report("METRIC|repair_power_electrical_retained|{}",
                  window.size());
  current = after;
  for (const Candidate &candidate : window) {
    accountAcceptedCandidate(candidate);
  }
  logger_->report("METRIC|repair_power_guarded_moves|{}", window.size());
  logger_->report("METRIC|repair_power_retained_gain|{:.6g}", retained_gain);
  if (cfg_.authoritative_timing_budget) {
    logger_->report("METRIC|repair_power_timing_retained|{}", window.size());
  }
  logger_->info(RSZ, 2337,
                "REPAIR_POWER|accept_late_window|phase={}|count={}|"
                "leak_gain={:.6g}|area_gain={:.6g}|cap_gain={:.6g}|"
                "window_size={}|wns={}|tns={}",
                cfg_.phase, window.size(), leakage_gain, area_gain,
                input_cap_gain, adaptive_window_size_,
                sta::delayAsString(after.wns, 3, sta_),
                sta::delayAsString(after.tns, 3, sta_));
  return true;
}

bool RepairPowerPolicy::withinPhaseBudget(const Metrics &before,
                                          const Metrics &after,
                                          const Metrics &baseline) const {
  if (cfg_.max_wns_drop > 0.0 && after.wns < baseline.wns - cfg_.max_wns_drop) {
    return false;
  }
  const double baseline_abs_tns =
      positiveOrDefault(std::abs(static_cast<double>(baseline.tns)), 1.0e-9);
  if (cfg_.max_tns_expand_ratio > 0.0) {
    const double allowed_abs_tns =
        baseline_abs_tns * (1.0 + cfg_.max_tns_expand_ratio);
    if (std::abs(static_cast<double>(after.tns)) > allowed_abs_tns) {
      return false;
    }
  }
  if (after.fanout_violations > baseline.fanout_violations) {
    return false;
  }
  if (before.leakage > 0.0 && before.area > 0.0 && after.leakage > 0.0 &&
      after.area > 0.0 && after.leakage > before.leakage + 1.0e-18 &&
      after.area >= before.area) {
    return false;
  }
  return true;
}

bool RepairPowerPolicy::withinMidAreaBudget(const Metrics &before,
                                            const Metrics &after,
                                            const Metrics &baseline) const {
  const bool post_route_sensitive_mid =
      std::abs(static_cast<double>(baseline.tns)) < 20.0e-9;
  if (cfg_.max_wns_drop > 0.0 &&
      (after.wns < before.wns - cfg_.max_wns_drop ||
       after.wns < baseline.wns - cfg_.max_wns_drop)) {
    return false;
  }
  if (tnsWorsenedBeyond(before.tns, after.tns, cfg_.max_tns_expand_ratio,
                        2.0e-12, 2.0e-9)) {
    return false;
  }
  if (tnsWorsenedBeyond(
          baseline.tns, after.tns,
          post_route_sensitive_mid ? 0.008 : cfg_.max_tns_expand_ratio * 2.0,
          5.0e-12, post_route_sensitive_mid ? 1.0e-10 : 3.0e-9)) {
    return false;
  }
  return after.fanout_violations <= baseline.fanout_violations;
}

bool RepairPowerPolicy::withinLateTimingBudget(const Metrics &before,
                                               const Metrics &after,
                                               const Metrics &baseline) const {
  if (cfg_.max_wns_drop > 0.0 &&
      (after.wns < before.wns - cfg_.max_wns_drop ||
       after.wns < baseline.wns - cfg_.max_wns_drop)) {
    return false;
  }
  if (tnsWorsenedBeyond(before.tns, after.tns, cfg_.max_tns_expand_ratio,
                        2.0e-12, 1.5e-9)) {
    return false;
  }
  if (tnsWorsenedBeyond(baseline.tns, after.tns,
                        cfg_.max_tns_expand_ratio * 2.0, 5.0e-12, 3.0e-9)) {
    return false;
  }
  return true;
}

bool RepairPowerPolicy::withinLateLeakageBudget(const Metrics &before,
                                                const Metrics &after,
                                                const Metrics &baseline) const {
  if (!withinLateTimingBudget(before, after, baseline)) {
    return false;
  }
  return after.fanout_violations <= baseline.fanout_violations;
}

bool RepairPowerPolicy::withinLateWeightedPowerTimingBudget(
    const Metrics &before, const Metrics &after,
    const Metrics &baseline) const {
  if (!withinLateLeakageBudget(before, after, baseline)) {
    return false;
  }

  const double baseline_leakage = positiveOrDefault(
      baseline.leakage, positiveOrDefault(before.leakage, 1.0));
  const double baseline_abs_tns = positiveOrDefault(
      std::abs(static_cast<double>(baseline.tns)),
      positiveOrDefault(std::abs(static_cast<double>(before.tns)), 1.0e-9));
  const double leakage_gain = before.leakage - after.leakage;
  const double tns_penalty = std::max(0.0, static_cast<double>(before.tns) -
                                               static_cast<double>(after.tns));
  if (leakage_gain <= 1.0e-18) {
    return false;
  }
  const double allowed_tns_penalty =
      std::min(2.0e-9, std::max(1.0e-11, baseline_abs_tns * 0.150));
  if (tns_penalty > allowed_tns_penalty) {
    return false;
  }
  const double power_drop_ratio = 100.0 * leakage_gain / baseline_leakage;
  const double tns_expand_ratio = 100.0 * tns_penalty / baseline_abs_tns;
  const double power_guard_score =
      power_drop_ratio * 250.0 - tns_expand_ratio * 0.10;

  return power_guard_score > cfg_.late_min_power_guard_score - 0.25;
}

double RepairPowerPolicy::scoreCandidate(const Candidate &candidate) const {
  const double power_gain = cfg_.leakage_weight * candidate.leakage_gain +
                            cfg_.area_weight * candidate.area_gain +
                            cfg_.input_cap_weight * candidate.input_cap_gain;
  const double crit_penalty =
      std::max(0.0, -candidate.target.slack) * cfg_.criticality_weight +
      std::max(0, candidate.target.fanout - 12) * 0.02;
  const double kind_bonus =
      candidate.kind == MoveKind::kRemoveBuffer             ? cfg_.buffer_bonus
      : candidate.kind == MoveKind::kSizeDown               ? 0.03
      : candidate.kind == MoveKind::kSizeDownAndPowerVtSwap ? 0.04
                                                            : 0.01;
  const double base_score = power_gain - crit_penalty + kind_bonus;
  if (cfg_.phase == "early_forced_reclaim" &&
      candidate.kind == MoveKind::kPowerVtSwap &&
      candidate.leakage_gain > 0.0 &&
      isStrictPowerDirectionVtSwap(candidate.target.cell,
                                   candidate.replacement)) {
    const int current_rank = powerVtRank(candidate.target.cell);
    const int candidate_rank = powerVtRank(candidate.replacement);
    const double rank_bonus =
        current_rank == 0 && candidate_rank == 2   ? 0.060
        : current_rank == 0 && candidate_rank == 1 ? 0.030
        : current_rank == 1 && candidate_rank == 2 ? 0.020
                                                   : 0.005;
    const double leakage_ratio =
        candidate.leakage_gain /
        positiveOrDefault(candidate.target.leakage, 1.0);
    const double leakage_bonus = std::min(0.030, leakage_ratio * 0.020);
    return std::max(base_score, 0.001 + rank_bonus + leakage_bonus);
  }
  return base_score;
}

void RepairPowerPolicy::markStabilitySampledTies(
    std::vector<Candidate> &candidates) const {
  int examined = 0;
  for (size_t begin = 0; begin < candidates.size();) {
    size_t end = begin + 1;
    while (end < candidates.size() &&
           candidates[begin].score == candidates[end].score) {
      ++end;
    }
    bool has_stability_tie = false;
    double first_slack = 0.0;
    bool have_first_slack = false;
    for (size_t i = begin; i < end; ++i) {
      if (!std::isfinite(candidates[i].target.slack)) {
        continue;
      }
      if (have_first_slack && candidates[i].target.slack != first_slack) {
        has_stability_tie = true;
        break;
      }
      first_slack = candidates[i].target.slack;
      have_first_slack = true;
    }
    if (has_stability_tie) {
      for (size_t i = begin; i < end; ++i) {
        if (std::isfinite(candidates[i].target.slack) &&
            !candidates[i].stability_sampled_tiebreak) {
          candidates[i].stability_sampled_tiebreak = true;
          ++examined;
        }
      }
    }
    begin = end;
  }
  if (examined > 0) {
    logger_->report("METRIC|repair_power_stability_sampled_examined|{}",
                    examined);
  }
}

bool RepairPowerPolicy::stabilitySampledPreferred(
    const Candidate &lhs, const Candidate &rhs) const {
  if (lhs.score != rhs.score) {
    return lhs.score > rhs.score;
  }
  const bool lhs_has_slack = std::isfinite(lhs.target.slack);
  const bool rhs_has_slack = std::isfinite(rhs.target.slack);
  if (lhs_has_slack != rhs_has_slack) {
    return lhs_has_slack;
  }
  if (!lhs_has_slack) {
    return false;
  }
  return lhs.target.slack > rhs.target.slack;
}

double
RepairPowerPolicy::scoreMidAreaCandidate(const Candidate &candidate) const {
  const double power_gain = cfg_.leakage_weight * candidate.leakage_gain +
                            cfg_.area_weight * candidate.area_gain +
                            cfg_.input_cap_weight * candidate.input_cap_gain;
  const double slack_bonus = slackBonus(candidate.target.slack, 4.0);
  const double fanout_penalty =
      std::max(0, candidate.target.fanout - 10) * 0.05;
  const double rank_bonus =
      isStrictPowerDirectionVtSwap(candidate.target.cell, candidate.replacement)
          ? 0.04
          : 0.0;
  return power_gain + slack_bonus + rank_bonus - fanout_penalty;
}

double
RepairPowerPolicy::scoreLateLeakageCandidate(const Candidate &candidate) const {
  const int current_rank = powerVtRank(candidate.target.cell);
  const int candidate_rank = powerVtRank(candidate.replacement);
  const double rank_bonus = current_rank == 0 && candidate_rank == 2   ? 0.30
                            : current_rank == 0 && candidate_rank == 1 ? 0.15
                            : current_rank == 1 && candidate_rank == 2 ? 0.08
                                                                       : 0.0;
  const double slack_bonus = slackBonus(candidate.target.slack, 8.0);
  const double fanout_penalty = std::max(0, candidate.target.fanout - 4) * 0.10;
  const double protected_slack_penalty =
      candidate.target.slack < cfg_.late_protected_slack ? 0.20 : 0.0;
  const double leakage_risk_bonus =
      candidate.leakage_gain / positiveOrDefault(candidate.target.leakage, 1.0);
  const double kind_bonus =
      candidate.kind == MoveKind::kRemoveBuffer ? cfg_.buffer_bonus * 0.50
      : candidate.kind == MoveKind::kSizeDownAndPowerVtSwap ? 0.18
      : candidate.kind == MoveKind::kSizeDown               ? 0.10
                                                            : 0.0;
  return cfg_.leakage_weight * candidate.leakage_gain +
         cfg_.area_weight * candidate.area_gain +
         cfg_.input_cap_weight * candidate.input_cap_gain + rank_bonus +
         kind_bonus + slack_bonus + leakage_risk_bonus - fanout_penalty -
         protected_slack_penalty;
}

sta::Pin *RepairPowerPolicy::findOutputPin(sta::Instance *inst) const {
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

sta::Pin *RepairPowerPolicy::findInputPin(sta::Instance *inst) const {
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

bool RepairPowerPolicy::touchesClock(sta::Instance *inst) const {
  std::unique_ptr<sta::InstancePinIterator> pin_iter(
      network_->pinIterator(inst));
  while (pin_iter->hasNext()) {
    sta::Pin *pin = pin_iter->next();
    if (sta_->isClock(pin, sta_->cmdMode())) {
      return true;
    }
    sta::Net *net = network_->net(pin);
    if (net != nullptr && sta_->isClock(net, sta_->cmdMode())) {
      return true;
    }
  }
  return false;
}

bool RepairPowerPolicy::isContestModifiableInstance(sta::Instance *inst) const {
  if (inst == nullptr || resizer_.dontTouch(inst) ||
      !resizer_.isLogicStdCell(inst) ||
      resizer_.drivesSequentialClockPin(inst)) {
    return false;
  }
  sta::LibertyCell *cell = network_->libertyCell(inst);
  if (cell == nullptr || resizer_.dontUse(cell) || cell->hasSequentials() ||
      cell->isClockCell() || cell->isClockGate()) {
    return false;
  }
  return !touchesClock(inst);
}

const sta::Pin *RepairPowerPolicy::findNetDriver(sta::Pin *load_pin) const {
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

double RepairPowerPolicy::worstOutputSlack(sta::Instance *inst) const {
  double worst = std::numeric_limits<double>::max();
  std::unique_ptr<sta::InstancePinIterator> pin_iter(
      network_->pinIterator(inst));
  while (pin_iter->hasNext()) {
    sta::Pin *pin = pin_iter->next();
    if (!network_->direction(pin)->isAnyOutput()) {
      continue;
    }
    sta::Vertex *vertex = graph_->pinDrvrVertex(pin);
    if (vertex != nullptr) {
      worst = std::min(worst, static_cast<double>(sta_->slack(vertex, max_)));
    }
  }
  return worst == std::numeric_limits<double>::max() ? 0.0 : worst;
}

int RepairPowerPolicy::outputFanout(sta::Pin *output_pin) const {
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

bool RepairPowerPolicy::passesBufferRemovalFanoutGuard(
    const Target &target) const {
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
  return cfg_.max_unbuffer_result_fanout <= 0 ||
         new_fanout <= cfg_.max_unbuffer_result_fanout;
}

bool RepairPowerPolicy::footprintOk(sta::LibertyCell *current,
                                    sta::LibertyCell *candidate) const {
  if (!cfg_.match_cell_footprint) {
    return true;
  }
  odb::dbMaster *curr = resizer_.dbNetwork()->staToDb(current);
  odb::dbMaster *cand = resizer_.dbNetwork()->staToDb(candidate);
  return curr != nullptr && cand != nullptr &&
         cand->getHeight() == curr->getHeight() &&
         cand->getWidth() <= curr->getWidth();
}

int RepairPowerPolicy::powerVtRank(sta::LibertyCell *cell) const {
  if (cell == nullptr) {
    return -1;
  }
  const std::string name = cell->name();
  auto ends_with = [&](const std::string &suffix) {
    return name.size() >= suffix.size() &&
           name.compare(name.size() - suffix.size(), suffix.size(), suffix) ==
               0;
  };
  if (ends_with("_SL") || ends_with("_SLVT") ||
      name.find("_ASAP7_75t_SL") != std::string::npos) {
    return 0;
  }
  if (ends_with("_L") || ends_with("_LVT") ||
      name.find("_ASAP7_75t_L") != std::string::npos) {
    return 1;
  }
  if (ends_with("_R") || ends_with("_RVT") ||
      name.find("_ASAP7_75t_R") != std::string::npos) {
    return 2;
  }
  return -1;
}

bool RepairPowerPolicy::isPowerDirectionVtSwap(
    sta::LibertyCell *current, sta::LibertyCell *candidate) const {
  const int current_rank = powerVtRank(current);
  const int candidate_rank = powerVtRank(candidate);
  if (current_rank >= 0 && candidate_rank >= 0) {
    return candidate_rank >= current_rank;
  }

  odb::dbMaster *curr = resizer_.dbNetwork()->staToDb(current);
  odb::dbMaster *cand = resizer_.dbNetwork()->staToDb(candidate);
  if (curr == nullptr || cand == nullptr ||
      resizer_.cellVTType(curr) == resizer_.cellVTType(cand)) {
    return true;
  }
  return false;
}

bool RepairPowerPolicy::isStrictPowerDirectionVtSwap(
    sta::LibertyCell *current, sta::LibertyCell *candidate) const {
  const int current_rank = powerVtRank(current);
  const int candidate_rank = powerVtRank(candidate);
  if (current_rank >= 0 && candidate_rank >= 0) {
    return candidate_rank > current_rank;
  }
  return isPowerDirectionVtSwap(current, candidate) &&
         cellLeakage(candidate) < cellLeakage(current);
}

RepairPowerPolicy::MoveKind
RepairPowerPolicy::classifyMove(sta::LibertyCell *current,
                                sta::LibertyCell *candidate) const {
  odb::dbMaster *curr = resizer_.dbNetwork()->staToDb(current);
  odb::dbMaster *cand = resizer_.dbNetwork()->staToDb(candidate);
  const int current_rank = powerVtRank(current);
  const int candidate_rank = powerVtRank(candidate);
  const bool named_vt_diff = current_rank >= 0 && candidate_rank >= 0 &&
                             current_rank != candidate_rank;
  const bool db_vt_diff =
      curr != nullptr && cand != nullptr &&
      resizer_.cellVTType(curr) != resizer_.cellVTType(cand);
  const bool vt_diff = named_vt_diff || db_vt_diff;
  const bool smaller = cellArea(candidate) < cellArea(current);
  if (vt_diff && smaller) {
    return MoveKind::kSizeDownAndPowerVtSwap;
  }
  if (vt_diff) {
    return MoveKind::kPowerVtSwap;
  }
  return MoveKind::kSizeDown;
}

bool RepairPowerPolicy::usefulCandidate(sta::LibertyCell *current,
                                        sta::LibertyCell *candidate,
                                        const MoveKind kind) const {
  const double leakage_gain = cellLeakage(current) - cellLeakage(candidate);
  const double area_gain = cellArea(current) - cellArea(candidate);
  const double cap_gain = inputCapProxy(current) - inputCapProxy(candidate);
  const bool meaningful_size_reclaim = area_gain > 0.0 || cap_gain > 0.0;
  if (leakage_gain < -1.0e-18 && !meaningful_size_reclaim) {
    return false;
  }
  if ((kind == MoveKind::kPowerVtSwap ||
       kind == MoveKind::kSizeDownAndPowerVtSwap) &&
      !isPowerDirectionVtSwap(current, candidate)) {
    return false;
  }
  if ((kind == MoveKind::kPowerVtSwap ||
       kind == MoveKind::kSizeDownAndPowerVtSwap) &&
      leakage_gain <= 0.0) {
    return false;
  }
  if (cfg_.phase == "early_forced_reclaim") {
    if (kind == MoveKind::kPowerVtSwap) {
      const double leakage_ratio =
          leakage_gain / positiveOrDefault(cellLeakage(current), 1.0);
      return accepted_power_vt_swap_ < cfg_.max_power_vt_swaps &&
             leakage_ratio >= cfg_.pure_vt_min_leakage_ratio &&
             cap_gain >= cfg_.pure_vt_min_cap_gain;
    }
    if (kind == MoveKind::kSizeDownAndPowerVtSwap && area_gain <= 0.0) {
      return false;
    }
  }
  if (kind == MoveKind::kSizeDown) {
    return resizer_.cellDriveResistance(candidate) >
               resizer_.cellDriveResistance(current) &&
           meaningful_size_reclaim;
  }
  return leakage_gain > 0.0 || area_gain > 0.0 || cap_gain > 0.0;
}

bool RepairPowerPolicy::excludeCriticalConeReversion(
    const Candidate &candidate) const {
  const bool positive_power_gain = candidate.leakage_gain > 0.0 ||
                                   candidate.area_gain > 0.0 ||
                                   candidate.input_cap_gain > 0.0;
  const bool admission_eligible =
      candidate.kind == MoveKind::kRemoveBuffer ||
      (candidate.replacement != nullptr &&
       resizer_.replacementPreservesMaxCap(candidate.target.inst,
                                           candidate.replacement));
  return cfg_.phase == "early_forced_reclaim" && positive_power_gain &&
         admission_eligible &&
         critical_timing_cone_.contains(candidate.target.inst);
}

void RepairPowerPolicy::captureCriticalTimingCone() {
  critical_timing_cone_.clear();
  compound_vt_critical_halo_.clear();
  compound_vt_critical_fanin_halo_.clear();
  if (cfg_.phase != "early_forced_reclaim") {
    return;
  }

  setup_context_.target_collector.init(0.0f);
  for (const rsz::Target &target :
       setup_context_.target_collector.collectCritPathDriverPinTargets(
           [this](sta::Pin *pin) {
             return isContestModifiableInstance(network_->instance(pin));
           })) {
    if (sta::Instance *inst = network_->instance(target.driver_pin)) {
      critical_timing_cone_.insert(inst);
    }
  }

  for (sta::Instance *inst : critical_timing_cone_) {
    std::unique_ptr<sta::InstancePinIterator> input_pin_iter(
        network_->pinIterator(inst));
    while (input_pin_iter->hasNext()) {
      sta::Pin *pin = input_pin_iter->next();
      if (!network_->direction(pin)->isAnyInput()) {
        continue;
      }
      const sta::Pin *driver_pin = findNetDriver(pin);
      sta::Instance *driver_inst = driver_pin != nullptr
                                       ? network_->instance(driver_pin)
                                       : nullptr;
      sta::LibertyCell *driver_cell = driver_inst != nullptr
                                          ? network_->libertyCell(driver_inst)
                                          : nullptr;
      if (driver_cell != nullptr && !driver_cell->hasSequentials()) {
        compound_vt_critical_fanin_halo_.insert(driver_inst);
      }
    }

    std::unique_ptr<sta::InstancePinIterator> pin_iter(
        network_->pinIterator(inst));
    while (pin_iter->hasNext()) {
      sta::Pin *pin = pin_iter->next();
      if (!network_->direction(pin)->isAnyOutput()) {
        continue;
      }
      sta::Net *net = network_->net(pin);
      if (net == nullptr) {
        continue;
      }
      std::unique_ptr<sta::NetConnectedPinIterator> load_iter(
          network_->connectedPinIterator(net));
      while (load_iter->hasNext()) {
        const sta::Pin *load_pin = load_iter->next();
        if (!network_->direction(load_pin)->isAnyInput()) {
          continue;
        }
        sta::Instance *load_inst = network_->instance(load_pin);
        sta::LibertyCell *load_cell =
            load_inst != nullptr ? network_->libertyCell(load_inst) : nullptr;
        if (load_cell != nullptr && !load_cell->hasSequentials()) {
          compound_vt_critical_halo_.insert(load_inst);
        }
      }
    }
  }
}

bool RepairPowerPolicy::excludeCompoundVtCriticalHalo(
    const Candidate &candidate) const {
  if (cfg_.phase != "early_forced_reclaim" ||
      candidate.kind != MoveKind::kSizeDownAndPowerVtSwap) {
    return false;
  }
  logger_->report("METRIC|repair_power_compound_halo_examined|1");
  if (!compound_vt_critical_halo_.contains(candidate.target.inst)) {
    return false;
  }
  logger_->report("METRIC|repair_power_compound_halo_excluded|1");
  return true;
}

bool RepairPowerPolicy::excludeCompoundVtCriticalFaninHalo(
    const Candidate &candidate) const {
  if (cfg_.phase != "early_forced_reclaim" ||
      candidate.kind != MoveKind::kSizeDownAndPowerVtSwap) {
    return false;
  }
  logger_->report("METRIC|repair_power_compound_fanin_halo_examined|1");
  if (!compound_vt_critical_fanin_halo_.contains(candidate.target.inst)) {
    return false;
  }
  logger_->report("METRIC|repair_power_compound_fanin_halo_excluded|1");
  return true;
}

bool RepairPowerPolicy::excludePureVtCriticalHalo(
    const Candidate &candidate) const {
  if (cfg_.phase != "early_forced_reclaim" ||
      candidate.kind != MoveKind::kPowerVtSwap) {
    return false;
  }
  logger_->report("METRIC|repair_power_pure_vt_halo_examined|1");
  if (!compound_vt_critical_halo_.contains(candidate.target.inst) &&
      !compound_vt_critical_fanin_halo_.contains(candidate.target.inst)) {
    return false;
  }
  logger_->report("METRIC|repair_power_pure_vt_halo_excluded|1");
  return true;
}

void RepairPowerPolicy::reportPersistentRetained(const int count) const {
  if (cfg_.phase == "early_forced_reclaim" && count > 0) {
    logger_->report("METRIC|repair_power_persistent_retained|{}", count);
  }
}

bool RepairPowerPolicy::candidateNeedsImmediateTimingGuard(
    const Candidate &candidate) const {
  return candidate.kind == MoveKind::kRemoveBuffer;
}

std::vector<RepairPowerPolicy::ElectricalState>
RepairPowerPolicy::captureElectricalState(
    const std::vector<Candidate> &candidates) const {
  std::unordered_set<const sta::Pin *> pins;
  std::unordered_set<const sta::Pin *> drivers;
  auto add_load_pins = [&](sta::Pin *output_pin) {
    sta::Net *net = network_->net(output_pin);
    if (net == nullptr) {
      return;
    }
    std::unique_ptr<sta::NetConnectedPinIterator> pin_iter(
        network_->connectedPinIterator(net));
    while (pin_iter->hasNext()) {
      const sta::Pin *pin = pin_iter->next();
      if (pin != output_pin && network_->direction(pin)->isAnyInput()) {
        pins.insert(pin);
      }
    }
  };
  for (const Candidate &candidate : candidates) {
    std::unique_ptr<sta::InstancePinIterator> pin_iter(
        network_->pinIterator(candidate.target.inst));
    while (pin_iter->hasNext()) {
      sta::Pin *pin = pin_iter->next();
      if (network_->direction(pin)->isAnyOutput()) {
        add_load_pins(pin);
        if (candidate.kind != MoveKind::kRemoveBuffer) {
          pins.insert(pin);
          drivers.insert(pin);
        }
      } else if (network_->direction(pin)->isAnyInput()) {
        const sta::Pin *driver = findNetDriver(pin);
        if (driver != nullptr) {
          pins.insert(driver);
          drivers.insert(driver);
        }
        if (candidate.kind != MoveKind::kRemoveBuffer) {
          pins.insert(pin);
        }
      }
    }
  }

  std::vector<ElectricalState> states;
  states.reserve(pins.size());
  for (const sta::Pin *pin : pins) {
    ElectricalState state;
    state.pin = pin;
    state.driver = drivers.contains(pin);
    float value = 0.0f;
    float limit = 0.0f;
    float slack = 0.0f;
    const sta::RiseFall *rise_fall = nullptr;
    const sta::Scene *scene = nullptr;
    if (state.driver) {
      sta_->checkCapacitance(pin, sta_->scenes(), max_, value, limit, slack,
                             rise_fall, scene);
      state.capacitance_violation = scene != nullptr && slack < 0.0f;
    }
    sta::Slew slew = 0.0f;
    sta_->checkSlew(pin, sta_->scenes(), max_, false, slew, limit, slack,
                    rise_fall, scene);
    state.slew_violation = scene != nullptr && slack < 0.0f;
    if (state.driver) {
      sta_->checkFanout(pin, sta_->cmdMode(), max_, value, limit, slack);
      state.fanout_violation = limit > 0.0f && slack < 0.0f;
    }
    states.push_back(state);
  }
  return states;
}

bool RepairPowerPolicy::introducesElectricalViolation(
    const std::vector<ElectricalState> &before) const {
  for (const ElectricalState &state : before) {
    float value = 0.0f;
    float limit = 0.0f;
    float slack = 0.0f;
    const sta::RiseFall *rise_fall = nullptr;
    const sta::Scene *scene = nullptr;
    if (state.driver) {
      sta_->checkCapacitance(state.pin, sta_->scenes(), max_, value, limit,
                             slack, rise_fall, scene);
      if (!state.capacitance_violation && scene != nullptr && slack < 0.0f) {
        return true;
      }
    }
    sta::Slew slew = 0.0f;
    sta_->checkSlew(state.pin, sta_->scenes(), max_, false, slew, limit,
                    slack, rise_fall, scene);
    if (!state.slew_violation && scene != nullptr && slack < 0.0f) {
      return true;
    }
    if (state.driver) {
      sta_->checkFanout(state.pin, sta_->cmdMode(), max_, value, limit, slack);
      if (!state.fanout_violation && limit > 0.0f && slack < 0.0f) {
        return true;
      }
    }
  }
  return false;
}

void RepairPowerPolicy::accountAcceptedCandidate(const Candidate &candidate) {
  if (cfg_.phase == "early_forced_reclaim" &&
      candidate.stability_sampled_tiebreak) {
    logger_->report("METRIC|repair_power_stability_sampled_retained|1");
  }
  if (cfg_.late_second_pass) {
    logger_->report("METRIC|repair_power_late_second_pass_retained|1");
  }
  switch (candidate.kind) {
  case MoveKind::kSizeDown:
    ++accepted_size_down_;
    break;
  case MoveKind::kPowerVtSwap:
    ++accepted_power_vt_swap_;
    break;
  case MoveKind::kSizeDownAndPowerVtSwap:
    ++accepted_size_down_vt_;
    if (cfg_.phase == "early_forced_reclaim") {
      logger_->report("METRIC|repair_power_compound_vt_retained|1");
    }
    break;
  case MoveKind::kRemoveBuffer:
    ++accepted_remove_buffer_;
    break;
  }
}

bool RepairPowerPolicy::withinCompoundVtQuota(
    const Candidate &candidate, const int pending_count) const {
  if (cfg_.phase != "early_forced_reclaim" ||
      candidate.kind != MoveKind::kSizeDownAndPowerVtSwap) {
    return true;
  }
  logger_->report("METRIC|repair_power_compound_vt_examined|1");
  return accepted_size_down_vt_ + pending_count <= cfg_.max_moves / 2;
}

void RepairPowerPolicy::noteAdaptiveWindowSuccess(const int accepted_count) {
  if (!cfg_.adaptive_timing_window || accepted_count <= 0 ||
      adaptive_window_size_ >= cfg_.max_timing_update_interval) {
    return;
  }
  consecutive_window_successes_ += 1;
  if (consecutive_window_successes_ < cfg_.adaptive_success_grow_threshold) {
    return;
  }

  const int old_size = adaptive_window_size_;
  adaptive_window_size_ = std::min(cfg_.max_timing_update_interval,
                                   std::max(old_size + 1, old_size * 2));
  consecutive_window_successes_ = 0;
  if (adaptive_window_size_ != old_size) {
    logger_->info(RSZ, 2330,
                  "REPAIR_POWER|adaptive_window|action=grow|from={}|to={}|"
                  "success_threshold={}",
                  old_size, adaptive_window_size_,
                  cfg_.adaptive_success_grow_threshold);
  }
}

void RepairPowerPolicy::noteAdaptiveWindowFailure(const char *reason) {
  if (!cfg_.adaptive_timing_window ||
      adaptive_window_size_ <= cfg_.min_timing_update_interval) {
    consecutive_window_successes_ = 0;
    return;
  }

  const int old_size = adaptive_window_size_;
  adaptive_window_size_ =
      std::max(cfg_.min_timing_update_interval, adaptive_window_size_ / 2);
  consecutive_window_successes_ = 0;
  if (adaptive_window_size_ != old_size) {
    logger_->info(RSZ, 2331,
                  "REPAIR_POWER|adaptive_window|action=shrink|from={}|to={}|"
                  "reason={}",
                  old_size, adaptive_window_size_,
                  reason != nullptr ? reason : "unknown");
  }
}

double RepairPowerPolicy::inputCapProxy(sta::LibertyCell *cell) const {
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

double RepairPowerPolicy::cellArea(sta::LibertyCell *cell) const {
  odb::dbMaster *master = resizer_.dbNetwork()->staToDb(cell);
  return master != nullptr ? static_cast<double>(master->getArea()) : 0.0;
}

double RepairPowerPolicy::cellLeakage(sta::LibertyCell *cell) const {
  return resizer_.cellLeakage(cell).value_or(0.0f);
}

const char *RepairPowerPolicy::moveKindName(const MoveKind kind) const {
  switch (kind) {
  case MoveKind::kSizeDown:
    return "size_down";
  case MoveKind::kPowerVtSwap:
    return "power_vt_swap";
  case MoveKind::kSizeDownAndPowerVtSwap:
    return "size_down_and_power_vt_swap";
  case MoveKind::kRemoveBuffer:
    return "remove_buffer";
  }
  return "unknown";
}

std::string RepairPowerPolicy::instName(sta::Instance *inst) const {
  return inst != nullptr ? network_->pathName(inst) : std::string("<null>");
}

void RepairPowerPolicy::recordReject(const std::string &reason) {
  ++reject_reasons_[reason.empty() ? std::string("unknown") : reason];
}

std::string RepairPowerPolicy::rejectSummary() const {
  if (reject_reasons_.empty()) {
    return "{}";
  }
  std::vector<std::pair<std::string, int>> reasons(reject_reasons_.begin(),
                                                   reject_reasons_.end());
  std::ranges::sort(reasons, [](const auto &lhs, const auto &rhs) {
    if (lhs.second != rhs.second) {
      return lhs.second > rhs.second;
    }
    return lhs.first < rhs.first;
  });
  std::ostringstream out;
  out << "{";
  for (size_t i = 0; i < std::min<size_t>(reasons.size(), 8); ++i) {
    if (i != 0) {
      out << ",";
    }
    out << reasons[i].first << ":" << reasons[i].second;
  }
  if (reasons.size() > 8) {
    out << ",...";
  }
  out << "}";
  return out.str();
}

} // namespace rsz
