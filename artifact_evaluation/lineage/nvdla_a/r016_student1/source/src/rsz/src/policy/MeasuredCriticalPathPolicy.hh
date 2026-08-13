// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#pragma once

#include <array>
#include <memory>
#include <unordered_set>
#include <vector>

#include "MoveCommitter.hh"
#include "MoveGenerator.hh"
#include "OptimizationPolicy.hh"
#include "OptimizerTypes.hh"
#include "RepairSetupContext.hh"
#include "rsz/Resizer.hh"

namespace sta {
class Path;
class Vertex;
}  // namespace sta

namespace rsz {

class MeasuredCriticalPathPolicy : public OptimizationPolicy
{
 public:
  MeasuredCriticalPathPolicy(Resizer& resizer,
                             MoveCommitter& committer,
                             RepairSetupContext& setup_context,
                             const OptimizerRunConfig& config);
  ~MeasuredCriticalPathPolicy() override;

  const char* name() const override { return "MeasuredCriticalPathPolicy"; }
  bool start() override;
  void iterate() override;

 private:
  struct CandidateScore
  {
    std::unique_ptr<MoveCandidate> candidate;
    Estimate estimate;
    MoveType type{MoveType::kCount};
  };

  struct TypeStats
  {
    int applicable{0};
    int generated{0};
    int legal{0};
    int committed{0};
    float best_score{0.0f};
  };

  struct CellCostMetrics
  {
    float area{0.0f};
    float leakage{0.0f};
    float input_cap{0.0f};
  };

  void buildGenerators();
  void finishRun(bool result);

  std::vector<sta::Vertex*> findWorstViolatingEndpoints(int max_count) const;
  sta::Path* findWorstSlackPath(sta::Vertex* endpoint) const;
  std::vector<Target> selectCriticalTargets(sta::Path* path) const;

  bool estimateBestOnPath(sta::Path* path, CandidateScore& best);
  Estimate measureCandidate(const Target& target,
                            MoveCandidate& candidate,
                            MoveType type) const;
  Estimate measureCandidateWithTiming(const Target& target,
                                      MoveCandidate& candidate,
                                      MoveType type,
                                      bool route_shadow_this_candidate) const;
  bool commitMeasuredCandidate(CandidateScore& best);

  float endpointSlack(const sta::Pin* endpoint_pin) const;
  float criticalEndpointTns(int max_count) const;
  CellCostMetrics costMetrics(sta::Instance* inst) const;
  CellCostMetrics sumCostMetrics(
      const std::vector<sta::Instance*>& instances) const;
  float measuredCostPenalty(MoveType type,
                            const CellCostMetrics& before,
                            const CellCostMetrics& after,
                            int move_count) const;
  float movePenalty(MoveType type) const;
  void recordApplicable(MoveType type);
  void recordGenerated(MoveType type, int count);
  void recordLegal(MoveType type, float score);
  void recordCommitted(MoveType type);
  void reportCandidateStats() const;
  bool useRouteShadow() const;
  bool useGlobalRouteBaseline() const;
  bool shouldRefreshGlobalRouteBaseline() const;
  void refreshRoutedTiming() const;
  void refreshGlobalRouteBaseline(bool save_guides) const;
  void updateEvaluationTiming() const;

  std::vector<std::unique_ptr<MoveGenerator>> generators_;
  int committed_moves_{0};
  mutable int routed_shadow_evals_{0};
  std::unordered_set<sta::Vertex*> exhausted_endpoints_;
  std::array<TypeStats, static_cast<int>(MoveType::kCount)> type_stats_{};
};

}  // namespace rsz
