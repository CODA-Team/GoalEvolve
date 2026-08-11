// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "OptimizationPolicy.hh"
#include "PowerRecoveryCandidate.hh"
#include "RepairSetupContext.hh"

namespace odb {
class dbMaster;
}

namespace sta {
class Instance;
class LibertyCell;
class Pin;
class Vertex;
} // namespace sta

namespace rsz {

class PowerRecoveryPlusPolicy : public OptimizationPolicy {
public:
  PowerRecoveryPlusPolicy(Resizer &resizer, MoveCommitter &committer,
                          RepairSetupContext &setup_context,
                          const OptimizerRunConfig &config);
  ~PowerRecoveryPlusPolicy() override;

  const char *name() const override { return "PowerRecoveryPlusPolicy"; }
  bool start() override;
  void iterate() override;

private:
  struct Config {
    std::string phase{"legacy_power_recovery_plus"};
    float proportion{1.0f};
    int max_moves{200};
    int max_targets{3000};
    int max_candidates_per_inst{4};
    int trial_limit{1200};
    int batch_size{1};
    double power_weight{100.0};
    double tns_weight{30.0};
    double wns_weight{0.0};
    double criticality_weight{8.0};
    double area_power_scale{0.0};
    double input_cap_power_scale{1.0e7};
    double min_power_gain{0.0};
    double min_power_guard_score{0.0};
    double buffer_removal_bonus{0.15};
    double min_target_slack{4.0e-11};
    double min_unbuffer_slack{4.0e-11};
    double max_tns_sacrifice_ratio{0.025};
    double max_wns_sacrifice{2.0e-12};
    int max_unbuffer_fanout{4};
    int max_unbuffer_result_fanout{4};
    bool match_cell_footprint{true};
    bool allow_negative_wns{true};
    bool enable_buffer_removal{true};
    bool enable_cell_recovery{true};
    bool enable_vt_recovery{true};
    bool greedy_commit{false};
    bool skip_sequential{true};
    bool require_leakage_gain{true};
    bool require_local_power_gain{false};
    bool verbose{false};
  };

  struct Metrics {
    sta::Slack wns{0.0};
    sta::Slack tns{0.0};
    double leakage_proxy{0.0};
    double area{0.0};
  };

  struct TargetInfo {
    sta::Instance *inst{nullptr};
    sta::Pin *output_pin{nullptr};
    sta::LibertyCell *current_cell{nullptr};
    float worst_slack{0.0f};
    int fanout{0};
    double leakage{0.0};
    double area{0.0};
    double input_cap{0.0};
    double priority{0.0};
  };

  struct TrialCandidate {
    TargetInfo target;
    sta::LibertyCell *candidate_cell{nullptr};
    PowerRecoveryMoveKind kind{PowerRecoveryMoveKind::kSizeDown};
    double leakage_gain{0.0};
    double area_gain{0.0};
    double input_cap_gain{0.0};
    double power_gain{0.0};
    double local_power_before{0.0};
    double local_power_after{0.0};
    sta::Slack wns_before{0.0};
    sta::Slack tns_before{0.0};
    sta::Slack wns_after{0.0};
    sta::Slack tns_after{0.0};
    double delta_wns{0.0};
    double delta_tns{0.0};
    double score{0.0};
    std::string reject_reason;
  };

  void loadConfig();
  int eligibleLogicInstanceCount() const;
  Metrics collectMetrics() const;
  std::vector<TargetInfo> collectTargets();
  std::vector<TrialCandidate> generateCandidates(const TargetInfo &target);
  bool measureCandidate(TrialCandidate &candidate, const Metrics &before);
  bool commitCandidate(const TrialCandidate &candidate);
  bool passesCatastrophicBudget(const TrialCandidate &candidate,
                                const Metrics &baseline) const;
  double scoreCandidate(const TrialCandidate &candidate, const Metrics &before,
                        const Metrics &baseline) const;

  sta::Pin *findOutputPin(sta::Instance *inst) const;
  sta::Pin *findInputPin(sta::Instance *inst) const;
  const sta::Pin *findNetDriver(sta::Pin *load_pin) const;
  float worstOutputSlack(sta::Instance *inst) const;
  int outputFanout(sta::Pin *output_pin) const;
  bool passesBufferRemovalFanoutGuard(const TargetInfo &target) const;
  double inputCapProxy(sta::LibertyCell *cell) const;
  double cellArea(sta::LibertyCell *cell) const;
  double cellLeakage(sta::LibertyCell *cell) const;
  bool footprintOk(sta::LibertyCell *current,
                   sta::LibertyCell *candidate) const;
  bool isPowerDirectionVtSwap(sta::LibertyCell *current,
                              sta::LibertyCell *candidate) const;
  PowerRecoveryMoveKind classifyMove(sta::LibertyCell *current,
                                     sta::LibertyCell *candidate) const;
  bool isUsefulPowerCandidate(sta::LibertyCell *current,
                              sta::LibertyCell *candidate,
                              PowerRecoveryMoveKind kind) const;
  double criticalityPenalty(const TargetInfo &target,
                            const TrialCandidate &candidate) const;
  void recordReject(const std::string &reason);
  std::string rejectReasonSummary() const;
  std::string instName(sta::Instance *inst) const;

  Config power_config_;
  Metrics baseline_;
  int committed_moves_{0};
  int trial_count_{0};
  int rejected_count_{0};
  int accepted_size_down_{0};
  int accepted_vt_recover_{0};
  int accepted_size_down_and_vt_{0};
  int accepted_remove_buffer_{0};
  std::unordered_map<std::string, int> reject_reason_counts_;
  std::unordered_set<sta::Instance *> exhausted_instances_;
};

} // namespace rsz
