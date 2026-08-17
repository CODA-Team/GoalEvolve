// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SetupLegacyMtPolicy.hh"

#include <cstddef>
#include <cstdlib>
#include <memory>
#include <optional>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

#include "BufferGenerator.hh"
#include "CloneGenerator.hh"
#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "OptimizationPolicy.hh"
#include "OptimizerTypes.hh"
#include "RerouteGenerator.hh"
#include "SizeDownGenerator.hh"
#include "SizeUpMatchGenerator.hh"
#include "SizeUpMtGenerator.hh"
#include "SplitLoadGenerator.hh"
#include "SwapPinsGenerator.hh"
#include "UnbufferGenerator.hh"
#include "VtSwapMtGenerator.hh"
#include "rsz/Resizer.hh"
#include "utl/Logger.h"
#include "utl/ThreadPool.h"

namespace rsz {

using utl::RSZ;

namespace {

constexpr float kGlobalSetupCriticalSlack = -6.0e-11f;
constexpr int kGlobalSetupUsefulBufferFanout = 4;
constexpr float kGlobalSetupSlackRichBufferFloor = 4.0e-11f;
constexpr float kPoolVeryCriticalSlack = -2.0e-10f;
constexpr float kWeakSizeupPrioritySlack = -1.0e-11f;

bool readEnvFlag(const char *name, const bool default_value) {
  const char *value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) != "0";
}

float readEnvFloat(const char *name, const float default_value) {
  const char *value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::strtof(value, nullptr);
}

bool hasBroadRecoveryPayoff(const Target &target, const Estimate &estimate) {
  return estimate.score > 0.0 || target.slack <= kGlobalSetupCriticalSlack;
}

bool isLowValueGlobalBuffer(const Target &target, const MoveType type) {
  return type == MoveType::kBuffer &&
         target.fanout <= kGlobalSetupUsefulBufferFanout &&
         target.slack > kGlobalSetupSlackRichBufferFloor;
}

bool crossTypeCandidatePoolEnabled() {
  return readEnvFlag("RSZ_MT_CROSS_TYPE_POOL", false);
}

bool isPooledMoveType(const MoveType type) {
  if (type == MoveType::kVtSwap || type == MoveType::kSizeUp) {
    return true;
  }
  if (!readEnvFlag("RSZ_MT_POOL_INCLUDE_STRUCTURAL", false)) {
    return false;
  }
  return type == MoveType::kClone || type == MoveType::kSplitLoad;
}

bool isWeakDriveCellName(const std::string &name) {
  return name.find("xp33") != std::string::npos ||
         name.find("xp5") != std::string::npos ||
         name.find("XP33") != std::string::npos ||
         name.find("XP5") != std::string::npos;
}

bool weakSizeupPriorityEnabled() {
  return readEnvFlag("RSZ_MT_PRIORITIZE_WEAK_SIZEUP", true);
}

float pooledScore(const Target &target, const MoveType type,
                  const Estimate &estimate) {
  float score = estimate.score;
  const float structural_bonus =
      readEnvFloat("RSZ_MT_POOL_STRUCTURAL_BONUS", 1.0e-12f);
  const float vt_penalty =
      readEnvFloat("RSZ_MT_POOL_VTSWAP_PENALTY", 5.0e-13f);
  switch (type) {
  case MoveType::kSizeUp:
    score += structural_bonus;
    break;
  case MoveType::kClone:
    score += structural_bonus * 1.25f;
    break;
  case MoveType::kSplitLoad:
    score += structural_bonus * 0.75f;
    break;
  case MoveType::kVtSwap:
    if (target.slack > kPoolVeryCriticalSlack) {
      score -= vt_penalty;
    }
    break;
  default:
    break;
  }
  return score;
}

} // namespace

SetupLegacyMtPolicy::SetupLegacyMtPolicy(Resizer &resizer,
                                         MoveCommitter &committer,
                                         RepairSetupContext &setup_context,
                                         const OptimizerRunConfig &config)
    : SetupLegacyPolicy(resizer, committer, setup_context, config) {
  is_experimental = true;
}

SetupLegacyMtPolicy::~SetupLegacyMtPolicy() = default;

bool SetupLegacyMtPolicy::start() {
  if (!SetupLegacyPolicy::start()) {
    return false;
  }
  thread_pool_ = makeWorkerThreadPool();
  return true;
}

void SetupLegacyMtPolicy::buildMoveGenerators(
    const std::vector<MoveType> &move_types, const GeneratorContext &context) {
  // Keep SetupLegacyBase's move sequence, but use MT-safe generators only for
  // VtSwap and SizeUp.
  move_generators_.clear();
  move_generators_.reserve(move_types.size());
  for (const MoveType type : move_types) {
    std::unique_ptr<MoveGenerator> generator;
    switch (type) {
    case MoveType::kVtSwap:
      generator = std::make_unique<VtSwapMtGenerator>(context);
      break;
    case MoveType::kSizeUp:
      generator = std::make_unique<SizeUpMtGenerator>(context);
      break;
    case MoveType::kSizeUpMatch:
      generator = std::make_unique<SizeUpMatchGenerator>(context);
      break;
    case MoveType::kBuffer:
      generator = std::make_unique<BufferGenerator>(context);
      break;
    case MoveType::kClone:
      generator = std::make_unique<CloneGenerator>(context);
      break;
    case MoveType::kSplitLoad:
      generator = std::make_unique<SplitLoadGenerator>(context);
      break;
    case MoveType::kSizeDown:
      generator = std::make_unique<SizeDownGenerator>(context);
      break;
    case MoveType::kSwapPins:
      generator = std::make_unique<SwapPinsGenerator>(context);
      break;
    case MoveType::kUnbuffer:
      generator = std::make_unique<UnbufferGenerator>(context);
      break;
    case MoveType::kReroute:
      generator = std::make_unique<RerouteGenerator>(context);
      break;
    case MoveType::kCount:
      break;
    }
    if (generator != nullptr) {
      move_generators_.push_back(std::move(generator));
    }
  }
}

bool SetupLegacyMtPolicy::canTryGenerator(
    const MoveGenerator &generator, const Target &target,
    const std::unordered_set<MoveType> *rejected_types) const {
  const MoveType type = generator.type();
  if (rejected_types != nullptr && rejected_types->contains(type)) {
    return false;
  }
  if (weakSizeupPriorityEnabled() &&
      (type == MoveType::kClone || type == MoveType::kSplitLoad) &&
      (rejected_types == nullptr ||
       !rejected_types->contains(MoveType::kSizeUp)) &&
      target.slack <= readEnvFloat("RSZ_MT_WEAK_SIZEUP_PRIORITY_SLACK",
                                   kWeakSizeupPrioritySlack)) {
    sta::LibertyPort *drvr_port = network_->libertyPort(target.driver_pin);
    sta::LibertyCell *drvr_cell =
        drvr_port != nullptr ? drvr_port->libertyCell() : nullptr;
    if (drvr_cell != nullptr && isWeakDriveCellName(drvr_cell->name())) {
      debugPrint(logger_, RSZ, "repair_setup", 2,
                 "Deferring {} for weak critical driver {} ({}); trying "
                 "sizeup first",
                 generator.name(), network_->pathName(target.driver_pin),
                 drvr_cell->name());
      return false;
    }
  }
  if (isLowValueGlobalBuffer(target, type)) {
    debugPrint(logger_, RSZ, "repair_setup", 2,
               "Rejecting buffer for {} in MT setup: slack-rich fanout {} "
               "has low global recovery value",
               network_->pathName(target.driver_pin), target.fanout);
    return false;
  }
  return generator.isApplicable(target);
}

void SetupLegacyMtPolicy::logConsideringGenerator(
    const MoveGenerator &generator, const Target &target) const {
  debugPrint(logger_, RSZ, "repair_setup", 1, "Considering {} for {}",
             generator.name(), network_->pathName(target.driver_pin));
}

bool SetupLegacyMtPolicy::usesMtCandidateScoring(const MoveType type) const {
  return type == MoveType::kVtSwap || type == MoveType::kSizeUp;
}

MoveCandidate *
SetupLegacyMtPolicy::estimateCandidatesMt(CandidateVector &candidates,
                                          std::vector<Estimate> &estimates,
                                          Estimate &best_estimate) {
  if (candidates.empty()) {
    return nullptr;
  }

  estimates = thread_pool_->parallelMap(
      candidates,
      [](const std::unique_ptr<MoveCandidate> &candidate) -> Estimate {
        return candidate->estimate();
      });

  MoveCandidate *best_candidate = nullptr;
  best_estimate = {};
  for (size_t index = 0; index < estimates.size(); ++index) {
    const Estimate &estimate = estimates[index];
    if (!estimate.legal) {
      continue;
    }
    if (best_candidate == nullptr || estimate.score > best_estimate.score) {
      best_candidate = candidates[index].get();
      best_estimate = estimate;
    }
  }

  return best_candidate;
}

bool SetupLegacyMtPolicy::estimateAndCommitCandidates(
    const Target &target, const MoveType type, CandidateVector &candidates,
    const int repairs_per_pass, int &changed,
    std::optional<MoveType> &accepted_type) {
  if (usesMtCandidateScoring(type)) {
    return estimateAndCommitMtCandidates(
        target, type, candidates, repairs_per_pass, changed, accepted_type);
  }
  return estimateAndCommitSerialCandidates(
      target, type, candidates, repairs_per_pass, changed, accepted_type);
}

bool SetupLegacyMtPolicy::estimateAndCommitMtCandidates(
    const Target &target, const MoveType type, CandidateVector &candidates,
    const int repairs_per_pass, int &changed,
    std::optional<MoveType> &accepted_type) {
  std::vector<Estimate> estimates;
  Estimate best_estimate;
  MoveCandidate *best_candidate =
      estimateCandidatesMt(candidates, estimates, best_estimate);
  if (best_candidate == nullptr) {
    return false;
  }
  if (!hasBroadRecoveryPayoff(target, best_estimate)) {
    debugPrint(logger_, RSZ, "repair_setup", 2,
               "Rejecting {} for {} in MT setup: score {} has no aggregate "
               "recovery payoff",
               moveName(best_candidate->type()),
               network_->pathName(target.driver_pin), best_estimate.score);
    return false;
  }

  const MoveResult result = commitCandidate(target, type, *best_candidate);
  if (!result.accepted) {
    return false;
  }

  accepted_type = result.type;
  changed += repairProgressIncrement(result.type, repairs_per_pass);
  return true;
}

bool SetupLegacyMtPolicy::estimateAndCommitSerialCandidates(
    const Target &target, const MoveType type, CandidateVector &candidates,
    const int repairs_per_pass, int &changed,
    std::optional<MoveType> &accepted_type) {
  struct ScoredCandidate {
    MoveCandidate *candidate;
    Estimate estimate;
  };

  std::vector<ScoredCandidate> ranked_candidates;
  ranked_candidates.reserve(candidates.size());
  for (std::unique_ptr<MoveCandidate> &candidate : candidates) {
    const Estimate estimate = candidate->estimate();
    if (!estimate.legal) {
      continue;
    }
    if (!hasBroadRecoveryPayoff(target, estimate)) {
      debugPrint(logger_, RSZ, "repair_setup", 2,
                 "Rejecting {} for {} in broad setup: score {} has no "
                 "aggregate recovery payoff",
                 moveName(candidate->type()),
                 network_->pathName(target.driver_pin), estimate.score);
      continue;
    }
    ranked_candidates.push_back({candidate.get(), estimate});
  }
  std::ranges::sort(ranked_candidates,
                    [](const ScoredCandidate &lhs,
                       const ScoredCandidate &rhs) {
                      return lhs.estimate.score > rhs.estimate.score;
                    });

  bool accepted_batch = false;
  std::optional<MoveType> accepted_move_type;
  for (ScoredCandidate &scored : ranked_candidates) {
    const MoveResult result = commitCandidate(target, type, *scored.candidate);
    if (!result.accepted) {
      continue;
    }

    accepted_batch = true;
    accepted_move_type = result.type;
    accepted_type = result.type;
    if (!allowsBatchRepair(result.type)) {
      changed += repairProgressIncrement(result.type, repairs_per_pass);
      return true;
    }
  }

  if (accepted_batch) {
    changed += repairProgressIncrement(*accepted_move_type, repairs_per_pass);
    return true;
  }
  return false;
}

bool SetupLegacyMtPolicy::estimateAndCommitSizeDownBatch(
    MoveGenerator &generator, const Target &target, const int repairs_per_pass,
    int &changed, std::optional<MoveType> &accepted_type) {
  bool accepted_batch = false;
  std::optional<MoveType> accepted_move_type;
  while (true) {
    CandidateVector candidates = generator.generate(target);
    if (candidates.empty()) {
      break;
    }

    int batch_changed = 0;
    std::optional<MoveType> batch_accepted_type;
    if (!estimateAndCommitCandidates(target, generator.type(), candidates,
                                     repairs_per_pass, batch_changed,
                                     batch_accepted_type)) {
      break;
    }

    accepted_batch = true;
    accepted_move_type = batch_accepted_type;
  }

  if (!accepted_batch) {
    return false;
  }

  changed += repairProgressIncrement(*accepted_move_type, repairs_per_pass);
  accepted_type = accepted_move_type;
  return true;
}

bool SetupLegacyMtPolicy::estimateAndCommitPooledCandidates(
    const Target &target, const int repairs_per_pass, int &changed,
    const std::unordered_set<MoveType> *rejected_types,
    std::optional<MoveType> &accepted_type) {
  struct CandidateBucket {
    MoveType type;
    CandidateVector candidates;
  };
  struct ScoredCandidate {
    MoveType type;
    MoveCandidate *candidate;
    Estimate estimate;
    float pool_score;
  };

  std::vector<CandidateBucket> buckets;
  std::vector<ScoredCandidate> ranked_candidates;

  for (const std::unique_ptr<MoveGenerator> &generator_ptr : move_generators_) {
    MoveGenerator &generator = *generator_ptr;
    const MoveType type = generator.type();
    if (!isPooledMoveType(type) ||
        !canTryGenerator(generator, target, rejected_types)) {
      continue;
    }

    logConsideringGenerator(generator, target);
    CandidateVector candidates = generator.generate(target);
    if (candidates.empty()) {
      continue;
    }

    CandidateBucket &bucket =
        buckets.emplace_back(CandidateBucket{type, std::move(candidates)});
    if (usesMtCandidateScoring(type)) {
      std::vector<Estimate> estimates =
          thread_pool_->parallelMap(
              bucket.candidates,
              [](const std::unique_ptr<MoveCandidate> &candidate) -> Estimate {
                return candidate->estimate();
              });
      for (size_t index = 0; index < estimates.size(); ++index) {
        const Estimate &estimate = estimates[index];
        if (!estimate.legal || !hasBroadRecoveryPayoff(target, estimate)) {
          continue;
        }
        ranked_candidates.push_back(
            {type, bucket.candidates[index].get(), estimate,
             pooledScore(target, type, estimate)});
      }
    } else {
      for (std::unique_ptr<MoveCandidate> &candidate : bucket.candidates) {
        const Estimate estimate = candidate->estimate();
        if (!estimate.legal || !hasBroadRecoveryPayoff(target, estimate)) {
          continue;
        }
        ranked_candidates.push_back(
            {type, candidate.get(), estimate, pooledScore(target, type, estimate)});
      }
    }
  }

  if (ranked_candidates.empty()) {
    return false;
  }

  std::ranges::sort(ranked_candidates,
                    [](const ScoredCandidate &lhs,
                       const ScoredCandidate &rhs) {
                      if (lhs.pool_score != rhs.pool_score) {
                        return lhs.pool_score > rhs.pool_score;
                      }
                      return lhs.estimate.score > rhs.estimate.score;
                    });

  for (ScoredCandidate &scored : ranked_candidates) {
    const MoveResult result =
        commitCandidate(target, scored.type, *scored.candidate);
    if (!result.accepted) {
      continue;
    }
    accepted_type = result.type;
    changed += repairProgressIncrement(result.type, repairs_per_pass);
    debugPrint(logger_, RSZ, "repair_setup", 2,
               "Accepted pooled {} for {} raw_score={} pool_score={}",
               moveName(result.type), network_->pathName(target.driver_pin),
               scored.estimate.score, scored.pool_score);
    return true;
  }

  return false;
}

MoveResult SetupLegacyMtPolicy::commitCandidate(const Target &target,
                                                const MoveType type,
                                                MoveCandidate &candidate) {
  committer_.trackMoveAttempt(target.driver_pin, type);
  return committer_.commit(candidate);
}

bool SetupLegacyMtPolicy::tryRepairTarget(
    const Target &target, const int repairs_per_pass, int &changed,
    const std::unordered_set<MoveType> *rejected_types,
    std::optional<MoveType> &accepted_type) {
  const Target prepared_target = OptimizationPolicy::prepareTarget(target);
  const bool use_cross_type_pool = crossTypeCandidatePoolEnabled();
  bool tried_cross_type_pool = false;

  for (const std::unique_ptr<MoveGenerator> &generator_ptr : move_generators_) {
    MoveGenerator &generator = *generator_ptr;
    if (use_cross_type_pool && isPooledMoveType(generator.type())) {
      if (!tried_cross_type_pool) {
        tried_cross_type_pool = true;
        if (estimateAndCommitPooledCandidates(prepared_target,
                                              repairs_per_pass,
                                              changed,
                                              rejected_types,
                                              accepted_type)) {
          return true;
        }
      }
      continue;
    }

    if (!canTryGenerator(generator, prepared_target, rejected_types)) {
      continue;
    }

    logConsideringGenerator(generator, prepared_target);
    if (allowsBatchRepair(generator.type())) {
      if (estimateAndCommitSizeDownBatch(generator, prepared_target,
                                         repairs_per_pass, changed,
                                         accepted_type)) {
        return true;
      }
      continue;
    }

    CandidateVector candidates = generator.generate(prepared_target);
    if (estimateAndCommitCandidates(prepared_target, generator.type(),
                                    candidates, repairs_per_pass, changed,
                                    accepted_type)) {
      return true;
    }
  }
  return false;
}

} // namespace rsz
