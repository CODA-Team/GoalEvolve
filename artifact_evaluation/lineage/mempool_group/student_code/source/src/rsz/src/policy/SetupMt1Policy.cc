// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SetupMt1Policy.hh"

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <memory>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

#include "MoveCandidate.hh"
#include "MoveCommitter.hh"
#include "MoveGenerator.hh"
#include "OptimizationPolicy.hh"
#include "OptimizerTypes.hh"
#include "RepairTargetCollector.hh"
#include "SizeUpMtGenerator.hh"
#include "VtSwapMtGenerator.hh"
#include "rsz/Resizer.hh"
#include "sta/Delay.hh"
#include "sta/Fuzzy.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/PortDirection.hh"
#include "utl/Logger.h"
#include "utl/ThreadPool.h"

namespace rsz {

SetupMt1Policy::SetupMt1Policy(Resizer& resizer,
                               MoveCommitter& committer,
                               RepairSetupContext& setup_context,
                               const OptimizerRunConfig& config)
    : OptimizationPolicy(resizer, committer, setup_context, config)
{
  is_experimental = true;
}

SetupMt1Policy::~SetupMt1Policy() = default;

bool SetupMt1Policy::start()
{
  OptimizationPolicy::start();
  move_sequence_.clear();
  if (!config_.skip_vt_swap && resizer_.vtCategoryCount() > 1) {
    move_sequence_.push_back(MoveType::kVtSwap);
  }
  move_sequence_.push_back(MoveType::kSizeUp);
  buildMoveGenerators(move_sequence_, makeGeneratorContext());
  committed_moves_ = 0;
  iteration_index_ = 0;
  measured_batches_ = 0;
  committed_batches_ = 0;
  journal_rollbacks_ = 0;
  retained_batches_ = 0;
  logger_->report("METRIC|timing_mt1_deep_batches|0");
  logger_->report("METRIC|timing_mt1_deep_committed|0");
  logger_->report("METRIC|timing_mt1_deep_journal_rollbacks|0");
  logger_->report("METRIC|timing_mt1_deep_retained|0");
  committer_.captureInitialSlackDistribution();
  committer_.captureOriginalEndpointSlack();
  thread_pool_ = makeWorkerThreadPool();
  return true;
}

void SetupMt1Policy::buildMoveGenerators(
    const std::vector<MoveType>& move_types,
    const GeneratorContext& context)
{
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
      case MoveType::kBuffer:
      case MoveType::kClone:
      case MoveType::kSizeUpMatch:
      case MoveType::kSizeDown:
      case MoveType::kSwapPins:
      case MoveType::kUnbuffer:
      case MoveType::kSplitLoad:
      case MoveType::kReroute:
      case MoveType::kCount:
        break;
    }
    if (generator != nullptr) {
      move_generators_.push_back(std::move(generator));
    }
  }
}

void SetupMt1Policy::iterate()
{
  if (converged_) {
    return;
  }

  // Stop immediately when setup is already clean at the start of a pass.
  if (!hasSetupViolations(config_, max_)) {
    finishRun(true);
    return;
  }

  committer_.capturePrePhaseSlack();
  if (iteration_index_ > 0) {
    // The first iteration inherits the legacy setup preamble from the
    // optimizer. Later iterations may need a lightweight cap-check warmup
    // after commit-time timing updates.
    prewarmStaForPrepareStage();
  }
  const sta::Slack tns_before = totalNegativeSlack(max_);

  // Find target cells
  std::vector<Target> target_pins = collectWorstEndpointTargets();
  if (finishIfNoValidTargetPin(target_pins)) {
    return;
  }

  // Prewarm shared cell-equivalence caches once for the full target batch.
  prewarmTargets(target_pins);

  // Prepare necessary per-target data for parallel execution.
  OptimizationPolicy::prepareTargets(target_pins);

  // Generate and estimate each prepared target.  Target-level scheduling stays
  // serial because some generator-side STA queries are not thread-safe; each
  // target still uses the ThreadPool for move-type and candidate fanout.
  std::vector<TargetEvaluation> evaluations
      = generateAndEstimateTargets(target_pins);

  // Batch commit & update timing
  commitAndUpdateTiming(target_pins, evaluations);

  const sta::Slack tns_after = totalNegativeSlack(max_);
  printTrackerIterationSummary();

  if (finishIfStopConditionReached(tns_before, tns_after)) {
    return;
  }

  ++iteration_index_;
}

std::vector<Target> SetupMt1Policy::collectWorstEndpointTargets() const
{
  target_collector_->init(config_.setup_slack_margin);
  return target_collector_->collectCritPathDriverPinTargets(
      [this](sta::Pin* pin) {
        return resizer_.isEditableLogicStdCell(network_->instance(pin));
      });
}

std::vector<TargetEvaluation> SetupMt1Policy::generateAndEstimateTargets(
    const std::vector<Target>& targets)
{
  std::vector<TargetEvaluation> evaluations;
  evaluations.reserve(targets.size());
  for (const Target& target : targets) {
    evaluations.push_back(generateAndEstimateTarget(target));
  }

  trackPreparedTargets(targets);

  return evaluations;
}

TargetEvaluation SetupMt1Policy::generateAndEstimateTarget(const Target& target)
{
  TargetEvaluation evaluation;
  evaluation.candidates = generateCandidates(target);
  evaluation.best_candidate = estimateCandidates(
      evaluation.candidates, evaluation.estimates, evaluation.best_estimate);
  return evaluation;
}

CandidateVector SetupMt1Policy::generateCandidates(const Target& target)
{
  // Preserve move type order while the shared pool materializes candidates
  // from the immutable prepare-stage snapshot.
  std::vector<CandidateVector> generated_batches = thread_pool_->parallelMap(
      move_generators_,
      [&target](
          const std::unique_ptr<MoveGenerator>& generator) -> CandidateVector {
        if (!generator->isApplicable(target)) {
          return {};
        }
        return generator->generate(target);
      });

  // Merge candidates into a single vector
  CandidateVector candidates;
  for (CandidateVector& batch : generated_batches) {
    candidates.reserve(candidates.size() + batch.size());
    std::ranges::move(batch, std::back_inserter(candidates));
  }
  return candidates;
}

MoveCandidate* SetupMt1Policy::estimateCandidates(
    CandidateVector& candidates,
    std::vector<Estimate>& estimates,
    Estimate& best_estimate)
{
  MoveCandidate* best_candidate = nullptr;

  // Keep one estimate task per candidate while letting the pool collect the
  // results back in candidate order.
  estimates = thread_pool_->parallelMap(
      candidates,
      [](const std::unique_ptr<MoveCandidate>& candidate) -> Estimate {
        return candidate->estimate();
      });

  // Score every generated candidate on the stale target snapshot before the
  // global ranker orders targets for sequential commit.
  CandidateVector::iterator candidate_itr = candidates.begin();
  for (const Estimate& estimate : estimates) {
    MoveCandidate* candidate = candidate_itr->get();
    ++candidate_itr;
    if (!estimate.legal) {
      continue;
    }
    if (best_candidate == nullptr || estimate.score > best_estimate.score) {
      best_candidate = candidate;
      best_estimate = estimate;
    }
  }

  return best_candidate;
}

int SetupMt1Policy::commitAndUpdateTiming(
    const std::vector<Target>& targets,
    std::vector<TargetEvaluation>& evaluations)
{
  int committed_in_iteration = 0;
  std::vector<TargetEvaluation*> ranked_evaluations;
  ranked_evaluations.reserve(evaluations.size());
  for (TargetEvaluation& evaluation : evaluations) {
    if (evaluation.hasBestCandidate()) {
      ranked_evaluations.push_back(&evaluation);
    }
  }

  // Commit the globally best stale estimates first, then stop at the move
  // budget or when a target reuses an already modified instance.
  std::ranges::stable_sort(
      ranked_evaluations,
      [](const TargetEvaluation* lhs, const TargetEvaluation* rhs) {
        // Descending sort by score; fuzzy-equal scores stay in original order
        // thanks to stable_sort.
        return sta::fuzzyLess(rhs->best_estimate.score,
                              lhs->best_estimate.score);
      });

  // Commit pass: apply each target's best candidate in score order, honoring
  // the move budget and deduplicating by instance so a stale evaluation
  // cannot overwrite an instance that was already modified this iteration.
  std::unordered_set<sta::Instance*> committed_instances;
  std::vector<sta::Instance*> evaluated_instances;
  evaluated_instances.reserve(ranked_evaluations.size());
  for (TargetEvaluation* evaluation_ptr : ranked_evaluations) {
    const size_t target_index
        = static_cast<size_t>(evaluation_ptr - evaluations.data());
    evaluated_instances.push_back(targets.at(target_index).inst(resizer_));
  }
  const std::vector<ElectricalState> electrical_before
      = captureElectricalState(evaluated_instances);
  const sta::Slack tns_before = totalNegativeSlack(max_);
  committer_.beginJournal();
  for (TargetEvaluation* evaluation_ptr : ranked_evaluations) {
    if (policy_config_.max_committed_moves > 0
        && committed_moves_ + committed_in_iteration
               >= policy_config_.max_committed_moves) {
      break;
    }

    TargetEvaluation& evaluation = *evaluation_ptr;
    const size_t target_index
        = static_cast<size_t>(evaluation_ptr - evaluations.data());
    const Target& refreshed_target = targets.at(target_index);
    sta::Instance* refreshed_inst = refreshed_target.inst(resizer_);
    if (committed_instances.contains(refreshed_inst)) {
      continue;
    }

    if (!commitBestCandidate(evaluation.bestCandidate(), refreshed_target)) {
      continue;
    }

    ++committed_in_iteration;
    committed_instances.insert(refreshed_inst);
  }

  if (committed_in_iteration == 0) {
    committer_.restoreJournal();
    return 0;
  }

  resizer_.updateParasiticsAndTiming();
  ++measured_batches_;
  logger_->report("METRIC|timing_mt1_deep_batches|1");
  ++committed_batches_;
  logger_->report("METRIC|timing_mt1_deep_committed|1");
  const sta::Slack tns_after = totalNegativeSlack(max_);
  if (!sta::fuzzyGreater(tns_after, tns_before)
      || introducesElectricalViolation(electrical_before)) {
    ++journal_rollbacks_;
    committer_.restoreJournal();
    logger_->report("METRIC|timing_mt1_deep_journal_rollbacks|1");
    return 0;
  }

  committer_.commitJournal();
  committed_moves_ += committed_in_iteration;
  ++retained_batches_;
  logger_->report("METRIC|timing_mt1_deep_retained|1");
  return committed_in_iteration;
}

bool SetupMt1Policy::commitBestCandidate(MoveCandidate& best_candidate,
                                         const Target& target)
{
  // Commit only the highest-scoring legal move.
  committer_.trackMoveAttempt(
      best_candidate, target.driver_pin, target.endpointPin(resizer_));

  const MoveResult result = committer_.commit(best_candidate);
  if (!result.accepted) {
    committer_.rejectTrackedMoves();
    return false;
  }

  logger_->info(utl::RSZ,
                kMsgPolicyCommittedMoves,
                "SetupMt1Policy committed {} / {} moves.",
                committed_moves_ + 1,
                policy_config_.max_committed_moves);
  return true;
}

std::vector<SetupMt1Policy::ElectricalState>
SetupMt1Policy::captureElectricalState(
    const std::vector<sta::Instance*>& instances) const
{
  std::unordered_set<const sta::Pin*> pins;
  std::unordered_set<const sta::Pin*> drivers;
  for (sta::Instance* inst : instances) {
    if (inst == nullptr) {
      continue;
    }
    std::unique_ptr<sta::InstancePinIterator> pin_iter(
        network_->pinIterator(inst));
    while (pin_iter->hasNext()) {
      sta::Pin* pin = pin_iter->next();
      if (network_->direction(pin)->isAnyOutput()) {
        pins.insert(pin);
        drivers.insert(pin);
        sta::Net* net = network_->net(pin);
        if (net != nullptr) {
          std::unique_ptr<sta::NetConnectedPinIterator> connected_pin_iter(
              network_->connectedPinIterator(net));
          while (connected_pin_iter->hasNext()) {
            const sta::Pin* connected_pin = connected_pin_iter->next();
            if (connected_pin != pin
                && network_->direction(connected_pin)->isAnyInput()) {
              pins.insert(connected_pin);
            }
          }
        }
      } else if (network_->direction(pin)->isAnyInput()) {
        pins.insert(pin);
        const sta::Pin* driver = findNetDriver(pin);
        if (driver != nullptr) {
          pins.insert(driver);
          drivers.insert(driver);
        }
      }
    }
  }

  std::vector<ElectricalState> states;
  states.reserve(pins.size());
  for (const sta::Pin* pin : pins) {
    ElectricalState state;
    state.pin = pin;
    state.driver = drivers.contains(pin);
    float value = 0.0f;
    float limit = 0.0f;
    float slack = 0.0f;
    const sta::RiseFall* rise_fall = nullptr;
    const sta::Scene* scene = nullptr;
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

bool SetupMt1Policy::introducesElectricalViolation(
    const std::vector<ElectricalState>& before) const
{
  for (const ElectricalState& state : before) {
    float value = 0.0f;
    float limit = 0.0f;
    float slack = 0.0f;
    const sta::RiseFall* rise_fall = nullptr;
    const sta::Scene* scene = nullptr;
    if (state.driver) {
      sta_->checkCapacitance(state.pin, sta_->scenes(), max_, value, limit,
                             slack, rise_fall, scene);
      if (!state.capacitance_violation && scene != nullptr && slack < 0.0f) {
        return true;
      }
    }
    sta::Slew slew = 0.0f;
    sta_->checkSlew(state.pin, sta_->scenes(), max_, false, slew, limit, slack,
                    rise_fall, scene);
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

const sta::Pin* SetupMt1Policy::findNetDriver(sta::Pin* load_pin) const
{
  sta::Net* net = network_->net(load_pin);
  if (net == nullptr) {
    return nullptr;
  }
  std::unique_ptr<sta::NetConnectedPinIterator> pin_iter(
      network_->connectedPinIterator(net));
  while (pin_iter->hasNext()) {
    const sta::Pin* pin = pin_iter->next();
    if (pin != load_pin && network_->isDriver(pin)) {
      return pin;
    }
  }
  return nullptr;
}

void SetupMt1Policy::trackPreparedTargets(const std::vector<Target>& targets)
{
  for (const Target& target : targets) {
    committer_.trackPreparedViolator(target);
  }
}

void SetupMt1Policy::printTrackerIterationSummary()
{
  committer_.printMoveSummary("MT1 Iteration Summary");
  committer_.printEndpointSummary("MT1 Iteration Endpoint Profiler");
  committer_.clearTrackedMoves();
}

bool SetupMt1Policy::finishIfNoValidTargetPin(
    const std::vector<Target>& targets)
{
  if (!targets.empty()) {
    return false;
  }

  finishRun(false);
  return true;
}

bool SetupMt1Policy::finishIfStopConditionReached(const sta::Slack tns_before,
                                                  const sta::Slack tns_after)
{
  // Re-evaluate global stop conditions after the current pass commits moves.

  // No more violation
  if (!hasSetupViolations(config_, max_)) {
    finishRun(true);
    return true;
  }

  // Reach the max number of moves
  if (policy_config_.max_committed_moves > 0
      && committed_moves_ >= policy_config_.max_committed_moves) {
    finishRun(false);
    return true;
  }

  // No TNS improvement
  if (!sta::fuzzyLess(tns_before, tns_after)) {
    finishRun(false);
    return true;
  }

  return false;
}

void SetupMt1Policy::finishRun(const bool result)
{
  markRunComplete(result);
}

}  // namespace rsz
