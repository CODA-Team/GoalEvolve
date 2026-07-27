// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#pragma once

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "OptimizationPolicy.hh"
#include "RepairSetupContext.hh"

namespace sta {
class Instance;
class LibertyCell;
class Pin;
} // namespace sta

namespace rsz {

class RepairPowerPolicy : public OptimizationPolicy {
public:
  RepairPowerPolicy(Resizer &resizer, MoveCommitter &committer,
                    RepairSetupContext &setup_context,
                    const OptimizerRunConfig &config);
  ~RepairPowerPolicy() override;

  const char *name() const override { return "RepairPowerPolicy"; }
  bool start() override;
  void iterate() override;

private:
  enum class MoveKind {
    kSizeDown,
    kPowerVtSwap,
    kSizeDownAndPowerVtSwap,
    kRemoveBuffer
  };

  struct Config {
    std::string phase{"early_forced_reclaim"};
    float proportion{0.8f};
    int max_moves{1000};
    int max_targets{8000};
    int trial_limit{8000};
    int batch_size{512};
    int max_unbuffer_fanout{3};
    int max_unbuffer_result_fanout{6};
    int max_unbuffer_moves{600};
    int max_power_vt_swaps{1800};
    int timing_update_interval{16};
    int min_timing_update_interval{4};
    int max_timing_update_interval{16};
    int max_revisit_passes{0};
    int adaptive_success_grow_threshold{24};
    int full_metrics_interval{256};
    double min_target_slack{-2.5e-11};
    double min_unbuffer_slack{2.0e-11};
    double max_tns_expand_ratio{8.0};
    double max_wns_drop{5.0e-10};
    double late_vt_min_target_slack{2.0e-11};
    double late_protected_slack{8.0e-11};
    double late_min_power_guard_score{0.0};
    int late_protected_fanout{6};
    double leakage_weight{1.0e6};
    double area_weight{1.0e-3};
    double input_cap_weight{2.0e7};
    double criticality_weight{2.0};
    double buffer_bonus{120.0};
    double pure_vt_min_leakage_ratio{0.01};
    double pure_vt_min_cap_gain{-1.0e-14};
    bool match_cell_footprint{true};
    bool skip_sequential{true};
    bool enable_vt_swap{true};
    bool adaptive_timing_window{true};
    bool verbose{false};
  };

  struct Metrics {
    sta::Slack wns{0.0};
    sta::Slack tns{0.0};
    double area{0.0};
    double leakage{0.0};
    int fanout_violations{0};
  };

  struct Target {
    sta::Instance *inst{nullptr};
    sta::Pin *output_pin{nullptr};
    sta::LibertyCell *cell{nullptr};
    double slack{0.0};
    int fanout{0};
    double leakage{0.0};
    double area{0.0};
    double input_cap{0.0};
    double priority{0.0};
  };

  struct Candidate {
    Target target;
    sta::LibertyCell *replacement{nullptr};
    MoveKind kind{MoveKind::kSizeDown};
    double leakage_gain{0.0};
    double area_gain{0.0};
    double input_cap_gain{0.0};
    double score{0.0};
  };

  void loadConfig();
  int eligibleLogicInstanceCount() const;
  Metrics collectMetrics() const;
  Metrics collectTimingMetrics() const;
  void iterateEarlyForcedReclaim();
  void iterateMidAreaReclaim();
  void iterateLateLeakageRecovery();
  std::vector<Target> collectTargets();
  std::vector<Target> collectMidAreaTargets();
  std::vector<Target> collectLateLeakageTargets();
  std::vector<Candidate> generateCandidates(const Target &target);
  std::vector<Candidate> generateMidAreaCandidates(const Target &target);
  std::vector<Candidate> generateLateLeakageCandidates(const Target &target);
  bool tryCommitCandidate(const Candidate &candidate, Metrics &current);
  bool tryCommitCandidateWindow(const std::vector<Candidate> &window,
                                Metrics &current);
  bool tryCommitGuardedSwap(const Candidate &candidate, Metrics &current);
  bool tryCommitGuardedSwapWindow(const std::vector<Candidate> &window,
                                  Metrics &current);
  bool tryCommitLateCandidate(const Candidate &candidate, Metrics &current);
  bool tryCommitLateWindow(const std::vector<Candidate> &window,
                           Metrics &current);
  bool withinPhaseBudget(const Metrics &before, const Metrics &after,
                         const Metrics &baseline) const;
  bool withinMidAreaBudget(const Metrics &before, const Metrics &after,
                           const Metrics &baseline) const;
  bool withinLateLeakageBudget(const Metrics &before, const Metrics &after,
                               const Metrics &baseline) const;
  bool withinLateWeightedPowerTimingBudget(const Metrics &before,
                                           const Metrics &after,
                                           const Metrics &baseline) const;
  double scoreCandidate(const Candidate &candidate) const;
  double scoreMidAreaCandidate(const Candidate &candidate) const;
  double scoreLateLeakageCandidate(const Candidate &candidate) const;

  sta::Pin *findOutputPin(sta::Instance *inst) const;
  sta::Pin *findInputPin(sta::Instance *inst) const;
  bool isContestModifiableInstance(sta::Instance *inst) const;
  bool touchesClock(sta::Instance *inst) const;
  const sta::Pin *findNetDriver(sta::Pin *load_pin) const;
  double worstOutputSlack(sta::Instance *inst) const;
  int outputFanout(sta::Pin *output_pin) const;
  bool passesBufferRemovalFanoutGuard(const Target &target) const;
  bool footprintOk(sta::LibertyCell *current,
                   sta::LibertyCell *candidate) const;
  int powerVtRank(sta::LibertyCell *cell) const;
  bool isPowerDirectionVtSwap(sta::LibertyCell *current,
                              sta::LibertyCell *candidate) const;
  bool isStrictPowerDirectionVtSwap(sta::LibertyCell *current,
                                    sta::LibertyCell *candidate) const;
  MoveKind classifyMove(sta::LibertyCell *current,
                        sta::LibertyCell *candidate) const;
  bool usefulCandidate(sta::LibertyCell *current, sta::LibertyCell *candidate,
                       MoveKind kind) const;
  bool candidateNeedsImmediateTimingGuard(const Candidate &candidate) const;
  void accountAcceptedCandidate(const Candidate &candidate);
  void noteAdaptiveWindowSuccess(int accepted_count);
  void noteAdaptiveWindowFailure(const char *reason);
  double inputCapProxy(sta::LibertyCell *cell) const;
  double cellArea(sta::LibertyCell *cell) const;
  double cellLeakage(sta::LibertyCell *cell) const;
  const char *moveKindName(MoveKind kind) const;
  std::string instName(sta::Instance *inst) const;
  void recordReject(const std::string &reason);
  std::string rejectSummary() const;

  Config cfg_;
  Metrics baseline_;
  int committed_{0};
  int trials_{0};
  int rejected_{0};
  int accepted_size_down_{0};
  int accepted_power_vt_swap_{0};
  int accepted_size_down_vt_{0};
  int accepted_remove_buffer_{0};
  int adaptive_window_size_{16};
  int consecutive_window_successes_{0};
  std::unordered_set<sta::Instance *> touched_;
  std::unordered_map<std::string, int> reject_reasons_;
};

} // namespace rsz
