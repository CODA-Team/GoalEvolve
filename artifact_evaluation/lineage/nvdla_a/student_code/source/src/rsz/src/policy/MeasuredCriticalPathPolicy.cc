// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "MeasuredCriticalPathPolicy.hh"

#include <array>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <optional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "BufferGenerator.hh"
#include "CloneGenerator.hh"
#include "MeasuredVtSwapGenerator.hh"
#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "RerouteCandidate.hh"
#include "RerouteGenerator.hh"
#include "SizeUpGenerator.hh"
#include "SplitLoadGenerator.hh"
#include "UnbufferGenerator.hh"
#include "db_sta/dbSta.hh"
#include "est/EstimateParasitics.h"
#include "grt/GlobalRouter.h"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/Delay.hh"
#include "sta/Fuzzy.hh"
#include "sta/Graph.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "sta/Path.hh"
#include "sta/PathExpanded.hh"
#include "sta/PortDirection.hh"
#include "sta/Search.hh"
#include "sta/Sta.hh"
#include "sta/TimingArc.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

namespace {

float envFloat(const char* name, const float default_value)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  char* end = nullptr;
  const float parsed = std::strtof(value, &end);
  return end != value && *end == '\0' ? parsed : default_value;
}

int envInt(const char* name, const int default_value)
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

bool containsMove(const std::vector<MoveType>& sequence, const MoveType move)
{
  if (sequence.empty()) {
    return true;
  }
  return std::find(sequence.begin(), sequence.end(), move) != sequence.end();
}

class ScopedEnv
{
 public:
  ScopedEnv(const char* name, const char* value) : name_(name)
  {
    const char* old_value = std::getenv(name);
    if (old_value != nullptr) {
      old_value_ = old_value;
    }
    setenv(name, value, 1);
  }

  ~ScopedEnv()
  {
    if (old_value_) {
      setenv(name_, old_value_->c_str(), 1);
    } else {
      unsetenv(name_);
    }
  }

 private:
  const char* name_;
  std::optional<std::string> old_value_;
};

float positiveGain(const float before, const float after)
{
  return std::max(0.0f, after - before);
}

float positiveOrDefault(const float value, const float default_value)
{
  return value > 0.0f && std::isfinite(value) ? value : default_value;
}

float growthRatio(const float before,
                  const float after,
                  const float default_scale)
{
  return std::max(0.0f, after - before)
         / positiveOrDefault(before, default_scale);
}

}  // namespace

MeasuredCriticalPathPolicy::MeasuredCriticalPathPolicy(
    Resizer& resizer,
    MoveCommitter& committer,
    RepairSetupContext& setup_context,
    const OptimizerRunConfig& config)
    : OptimizationPolicy(resizer, committer, setup_context, config)
{
  is_experimental = true;
}

MeasuredCriticalPathPolicy::~MeasuredCriticalPathPolicy() = default;

bool MeasuredCriticalPathPolicy::start()
{
  OptimizationPolicy::start();
  buildGenerators();
  committed_moves_ = 0;
  routed_shadow_evals_ = 0;
  exhausted_endpoints_.clear();
  type_stats_ = {};
  if (useGlobalRouteBaseline()) {
    refreshGlobalRouteBaseline(false);
    logger_->info(utl::RSZ,
                  2026,
                  "MeasuredCriticalPathPolicy using global-routing baseline timing.");
  }
  if (generators_.empty()) {
    finishRun(!hasSetupViolations(config_, max_));
  }
  return true;
}

void MeasuredCriticalPathPolicy::buildGenerators()
{
  generators_.clear();
  const GeneratorContext context = makeGeneratorContext();

  if (containsMove(config_.sequence, MoveType::kSizeUp)) {
    generators_.push_back(std::make_unique<SizeUpGenerator>(context));
  }
  if (!config_.skip_vt_swap && resizer_.vtCategoryCount() >= 2
      && containsMove(config_.sequence, MoveType::kVtSwap)) {
    generators_.push_back(std::make_unique<MeasuredVtSwapGenerator>(context));
  }
  if (!config_.skip_buffering
      && containsMove(config_.sequence, MoveType::kBuffer)) {
    generators_.push_back(std::make_unique<BufferGenerator>(context));
  }
  if (!config_.skip_buffering
      && containsMove(config_.sequence, MoveType::kSplitLoad)) {
    generators_.push_back(std::make_unique<SplitLoadGenerator>(context));
  }
  if (!config_.skip_buffer_removal
      && containsMove(config_.sequence, MoveType::kUnbuffer)) {
    generators_.push_back(std::make_unique<UnbufferGenerator>(context));
  }
  if (!config_.skip_gate_cloning
      && containsMove(config_.sequence, MoveType::kClone)) {
    generators_.push_back(std::make_unique<CloneGenerator>(context));
  }
  if (containsMove(config_.sequence, MoveType::kReroute)) {
    generators_.push_back(std::make_unique<RerouteGenerator>(context));
  }
}

void MeasuredCriticalPathPolicy::iterate()
{
  if (converged_) {
    return;
  }
  if (useGlobalRouteBaseline()) {
    refreshGlobalRouteBaseline(false);
  }
  if (!hasSetupViolations(config_, max_)) {
    finishRun(true);
    return;
  }

  const int max_moves = envInt("RSZ_MEASURED_CRIT_MAX_MOVES", 40);
  const int endpoints_per_round
      = std::max(1, envInt("RSZ_MEASURED_CRIT_ENDPOINTS_PER_ROUND", 1));
  bool accepted_any = false;
  while (hasSetupViolations(config_, max_)
         && (max_moves <= 0 || committed_moves_ < max_moves)) {
    if (useGlobalRouteBaseline()) {
      const int interval
          = std::max(1, envInt("RSZ_MEASURED_CRIT_GLOBAL_ROUTE_REFRESH", 12));
      if (committed_moves_ == 0 || committed_moves_ % interval == 0) {
        refreshGlobalRouteBaseline(false);
      }
    }
    std::vector<sta::Vertex*> endpoints
        = findWorstViolatingEndpoints(endpoints_per_round);
    if (endpoints.empty()) {
      break;
    }

    CandidateScore best;
    std::vector<sta::Vertex*> failed_endpoints;
    for (sta::Vertex* endpoint : endpoints) {
      sta::Path* path = findWorstSlackPath(endpoint);
      if (path == nullptr) {
        failed_endpoints.push_back(endpoint);
        continue;
      }

      CandidateScore endpoint_best;
      if (!estimateBestOnPath(path, endpoint_best)) {
        failed_endpoints.push_back(endpoint);
        continue;
      }
      if (best.candidate == nullptr
          || endpoint_best.estimate.score > best.estimate.score) {
        best = std::move(endpoint_best);
      }
    }

    if (best.candidate == nullptr || !commitMeasuredCandidate(best)) {
      for (sta::Vertex* endpoint : failed_endpoints) {
        exhausted_endpoints_.insert(endpoint);
      }
      continue;
    }
    accepted_any = true;
  }

  finishRun(!hasSetupViolations(config_, max_) || accepted_any);
}

std::vector<sta::Vertex*> MeasuredCriticalPathPolicy::findWorstViolatingEndpoints(
    const int max_count) const
{
  target_collector_->init(config_.setup_slack_margin);
  target_collector_->collectViolatingEndpoints();

  std::vector<std::pair<sta::Slack, sta::Vertex*>> endpoints;
  for (const auto& [endpoint_pin, endpoint_slack] :
       target_collector_->getViolatingEndpoints()) {
    sta::Vertex* endpoint = resizer_.graph()->pinLoadVertex(endpoint_pin);
    if (endpoint == nullptr || exhausted_endpoints_.contains(endpoint)) {
      continue;
    }
    if (sta::fuzzyLess(endpoint_slack, config_.setup_slack_margin)) {
      endpoints.emplace_back(endpoint_slack, endpoint);
    }
  }
  std::ranges::sort(endpoints,
                    [](const auto& lhs, const auto& rhs) {
                      return lhs.first < rhs.first;
                    });

  std::vector<sta::Vertex*> selected;
  selected.reserve(std::min<int>(max_count, endpoints.size()));
  for (const auto& [slack, endpoint] : endpoints) {
    selected.push_back(endpoint);
    if (selected.size() >= static_cast<size_t>(max_count)) {
      break;
    }
  }
  return selected;
}

sta::Path* MeasuredCriticalPathPolicy::findWorstSlackPath(
    sta::Vertex* endpoint) const
{
  return target_collector_->findWorstSlackPath(endpoint);
}

std::vector<Target> MeasuredCriticalPathPolicy::selectCriticalTargets(
    sta::Path* path) const
{
  std::vector<Target> path_targets = target_collector_->collectPathDriverTargets(
      path, path->slack(resizer_.staState()));
  if (path_targets.empty()) {
    return {};
  }

  sta::PathExpanded expanded(path, resizer_.staState());
  const sta::Scene* scene = path->scene(resizer_.staState());
  const int dcalc_ap = scene->dcalcAnalysisPtIndex(max_);

  std::vector<std::pair<sta::Delay, Target>> ranked_targets;
  ranked_targets.reserve(path_targets.size());
  for (const Target& path_target : path_targets) {
    if (!path_target.canBePathDriver()) {
      continue;
    }
    const int index = path_target.path_index;
    if (index < 0 || index >= expanded.size()) {
      continue;
    }

    const sta::Path* driver_path = expanded.path(index);
    const sta::TimingArc* prev_arc = driver_path->prevArc(resizer_.staState());
    sta::Edge* prev_edge = driver_path->prevEdge(resizer_.staState());
    if (prev_arc == nullptr || prev_edge == nullptr) {
      continue;
    }

    sta::Delay stage_delay
        = resizer_.graph()->arcDelay(prev_edge, prev_arc, dcalc_ap);
    if (index + 1 < expanded.size()) {
      const sta::Path* next_path = expanded.path(index + 1);
      sta::Edge* next_prev_edge = next_path->prevEdge(resizer_.staState());
      if (next_prev_edge != nullptr && next_prev_edge->isWire()) {
        sta::delayIncr(stage_delay,
                       sta::delayDiff(next_path->arrival(),
                                      driver_path->arrival(),
                                      resizer_.staState()),
                       resizer_.staState());
      }
    }
    ranked_targets.emplace_back(stage_delay, path_target);
  }

  std::ranges::sort(ranked_targets,
                    [](const auto& lhs, const auto& rhs) {
                      return lhs.first > rhs.first;
                    });

  const int max_targets
      = std::max(1, envInt("RSZ_MEASURED_CRIT_TARGETS_PER_PATH", 4));
  std::vector<Target> selected;
  selected.reserve(std::min<int>(max_targets, ranked_targets.size()));
  auto add_target = [&selected](const Target& target) {
    const bool already_selected
        = std::any_of(selected.begin(),
                      selected.end(),
                      [&target](const Target& existing) {
                        return existing.driver_pin == target.driver_pin;
                      });
    if (!already_selected) {
      selected.push_back(target);
    }
  };
  for (const auto& [delay, target] : ranked_targets) {
    add_target(target);
    if (selected.size() >= static_cast<size_t>(max_targets)) {
      break;
    }
  }

  const int max_fanout_targets
      = std::max(0, envInt("RSZ_MEASURED_CRIT_FANOUT_TARGETS_PER_PATH", 2));
  if (max_fanout_targets > 0) {
    std::vector<Target> fanout_targets;
    fanout_targets.reserve(path_targets.size());
    for (const Target& path_target : path_targets) {
      if (path_target.canBePathDriver() && path_target.fanout > 1) {
        fanout_targets.push_back(path_target);
      }
    }
    std::ranges::sort(fanout_targets,
                      [](const Target& lhs, const Target& rhs) {
                        return lhs.fanout > rhs.fanout;
                      });
    int added = 0;
    for (const Target& target : fanout_targets) {
      const size_t before_size = selected.size();
      add_target(target);
      if (selected.size() != before_size && ++added >= max_fanout_targets) {
        break;
      }
    }
  }

  const int max_buffer_targets =
      std::max(0, envInt("RSZ_MEASURED_CRIT_BUFFER_TARGETS_PER_PATH",
                         containsMove(config_.sequence, MoveType::kUnbuffer) ? 4
                                                                              : 0));
  if (max_buffer_targets > 0) {
    const int max_unbuffer_target_fanout = std::max(
        1, envInt("RSZ_MEASURED_CRIT_UNBUFFER_TARGET_MAX_FANOUT", 4));
    int added = 0;
    for (const auto& [delay, target] : ranked_targets) {
      if (!target.canBePathDriver() || target.path_index < 2) {
        continue;
      }
      if (target.fanout > max_unbuffer_target_fanout) {
        continue;
      }
      sta::Instance* inst = target.inst(resizer_);
      sta::LibertyCell* cell =
          inst != nullptr ? resizer_.network()->libertyCell(inst) : nullptr;
      if (cell == nullptr || !cell->isBuffer()) {
        continue;
      }
      if (!resizer_.canRemoveBuffer(inst, true)) {
        continue;
      }
      const size_t before_size = selected.size();
      add_target(target);
      if (selected.size() != before_size && ++added >= max_buffer_targets) {
        break;
      }
    }
  }

  const int max_tail_targets
      = std::max(0, envInt("RSZ_MEASURED_CRIT_TAIL_TARGETS_PER_PATH",
                           useGlobalRouteBaseline() ? 3 : 0));
  if (max_tail_targets > 0 && selected.size() < path_targets.size()) {
    int added = 0;
    for (auto iter = path_targets.rbegin(); iter != path_targets.rend();
         ++iter) {
      if (iter->canBePathDriver()) {
        const size_t before_size = selected.size();
        add_target(*iter);
        if (selected.size() != before_size && ++added >= max_tail_targets) {
          break;
        }
      }
    }
  }

  const int max_route_targets
      = std::max(0, envInt("RSZ_MEASURED_CRIT_ROUTE_TARGETS_PER_PATH",
                           useGlobalRouteBaseline() ? 3 : 0));
  if (max_route_targets > 0 && selected.size() < path_targets.size()) {
    std::vector<std::pair<sta::Delay, Target>> wire_targets;
    wire_targets.reserve(path_targets.size());
    for (const Target& path_target : path_targets) {
      const int index = path_target.path_index;
      if (!path_target.canBePathDriver() || index < 0
          || index + 1 >= expanded.size()) {
        continue;
      }
      const sta::Path* driver_path = expanded.path(index);
      const sta::Path* next_path = expanded.path(index + 1);
      if (driver_path == nullptr || next_path == nullptr) {
        continue;
      }
      sta::Edge* next_prev_edge = next_path->prevEdge(resizer_.staState());
      if (next_prev_edge == nullptr || !next_prev_edge->isWire()) {
        continue;
      }
      const sta::Delay wire_delay = sta::delayDiff(next_path->arrival(),
                                                   driver_path->arrival(),
                                                   resizer_.staState());
      wire_targets.emplace_back(wire_delay, path_target);
    }
    std::ranges::sort(wire_targets,
                      [](const auto& lhs, const auto& rhs) {
                        return lhs.first > rhs.first;
                      });
    int added = 0;
    for (const auto& [delay, target] : wire_targets) {
      const size_t before_size = selected.size();
      add_target(target);
      if (selected.size() != before_size && ++added >= max_route_targets) {
        break;
      }
    }
  }
  return selected;
}

bool MeasuredCriticalPathPolicy::estimateBestOnPath(sta::Path* path,
                                                    CandidateScore& best)
{
  std::vector<Target> targets = selectCriticalTargets(path);
  for (const Target& target : targets) {
    for (const std::unique_ptr<MoveGenerator>& generator_ptr : generators_) {
      MoveGenerator& generator = *generator_ptr;
      const MoveType type = generator.type();
      if (type == MoveType::kSplitLoad) {
        const int max_split_moves =
            envInt("RSZ_MEASURED_CRIT_SPLIT_MAX_MOVES", 14);
        const int split_index = static_cast<int>(MoveType::kSplitLoad);
        if (max_split_moves > 0
            && type_stats_[split_index].committed >= max_split_moves) {
          continue;
        }
        const int min_split_fanout =
            envInt("RSZ_MEASURED_CRIT_SPLIT_MIN_FANOUT", 5);
        const float low_fanout_slack =
            envFloat("RSZ_MEASURED_CRIT_SPLIT_LOW_FANOUT_SLACK", -3.0e-10f);
        if (target.fanout < min_split_fanout
            && target.slack > low_fanout_slack) {
          continue;
        }
      } else if (type == MoveType::kBuffer) {
        const int max_buffer_moves =
            envInt("RSZ_MEASURED_CRIT_BUFFER_MAX_MOVES", 6);
        const int buffer_index = static_cast<int>(MoveType::kBuffer);
        if (max_buffer_moves > 0
            && type_stats_[buffer_index].committed >= max_buffer_moves) {
          continue;
        }
        const int min_buffer_fanout =
            envInt("RSZ_MEASURED_CRIT_BUFFER_MIN_FANOUT", 7);
        const float low_fanout_slack =
            envFloat("RSZ_MEASURED_CRIT_BUFFER_LOW_FANOUT_SLACK", -3.5e-10f);
        if (target.fanout < min_buffer_fanout
            && target.slack > low_fanout_slack) {
          continue;
        }
      } else if (type == MoveType::kUnbuffer) {
        const int max_unbuffer_moves =
            envInt("RSZ_MEASURED_CRIT_UNBUFFER_MAX_MOVES", 48);
        const int unbuffer_index = static_cast<int>(MoveType::kUnbuffer);
        if (max_unbuffer_moves > 0
            && type_stats_[unbuffer_index].committed >= max_unbuffer_moves) {
          continue;
        }
      }
      if (!generator.isApplicable(target)) {
        continue;
      }
      recordApplicable(generator.type());
      std::vector<std::unique_ptr<MoveCandidate>> candidates;
      if (generator.type() == MoveType::kSplitLoad) {
        ScopedEnv low_fanout_split_env("RSZ_SPLIT_LOAD_ALLOW_LOW_FANOUT", "1");
        candidates = generator.generate(target);
      } else {
        candidates = generator.generate(target);
      }
      recordGenerated(generator.type(), candidates.size());
      for (auto& candidate : candidates) {
        Estimate estimate = measureCandidate(target, *candidate, generator.type());
        if (!estimate.legal) {
          continue;
        }
        recordLegal(generator.type(), estimate.score);
        if (best.candidate == nullptr || estimate.score > best.estimate.score) {
          best.candidate = std::move(candidate);
          best.estimate = estimate;
          best.type = generator.type();
        }
      }
      if (useGlobalRouteBaseline() && best.candidate != nullptr) {
        return true;
      }
    }
    if (useGlobalRouteBaseline() && best.candidate != nullptr) {
      return true;
    }
  }
  return best.candidate != nullptr;
}

Estimate MeasuredCriticalPathPolicy::measureCandidate(const Target& target,
                                                      MoveCandidate& candidate,
                                                      const MoveType type) const
{
  const bool route_shadow = useRouteShadow();
  const int max_route_shadow_evals
      = std::max(0, envInt("RSZ_MEASURED_CRIT_ROUTE_SHADOW_MAX_EVALS", 80));
  const bool route_shadow_this_candidate
      = route_shadow && routed_shadow_evals_ < max_route_shadow_evals;
  if (route_shadow && !route_shadow_this_candidate
      && envInt("RSZ_MEASURED_CRIT_REQUIRE_ROUTE_SHADOW", 0) != 0) {
    return {.legal = false, .score = 0.0f};
  }

  if (route_shadow_this_candidate
      && envInt("RSZ_MEASURED_CRIT_ROUTE_PREFILTER", 0) != 0) {
    const Estimate quick_estimate
        = measureCandidateWithTiming(target, candidate, type, false);
    const float min_prefilter_score = envFloat(
        "RSZ_MEASURED_CRIT_ROUTE_PREFILTER_MIN_SCORE", 0.10e-13f);
    if (!quick_estimate.legal || quick_estimate.score < min_prefilter_score) {
      return quick_estimate;
    }
  }

  const bool global_route_recheck
      = !route_shadow_this_candidate && useGlobalRouteBaseline()
        && envInt("RSZ_MEASURED_CRIT_GLOBAL_CANDIDATE_ROUTE", 0) != 0
        && envInt("RSZ_MEASURED_CRIT_GLOBAL_ROUTE_PREFILTER", 1) != 0;
  if (global_route_recheck) {
    ScopedEnv disable_global_eval("RSZ_MEASURED_CRIT_GLOBAL_CANDIDATE_EVAL",
                                  "0");
    ScopedEnv disable_global_route("RSZ_MEASURED_CRIT_GLOBAL_CANDIDATE_ROUTE",
                                   "0");
    const Estimate quick_estimate
        = measureCandidateWithTiming(target, candidate, type, false);
    const float min_prefilter_score = envFloat(
        "RSZ_MEASURED_CRIT_GLOBAL_ROUTE_PREFILTER_MIN_SCORE", 0.20e-13f);
    if (!quick_estimate.legal || quick_estimate.score < min_prefilter_score) {
      return quick_estimate;
    }
  }

  Estimate estimate = measureCandidateWithTiming(
      target, candidate, type, route_shadow_this_candidate);
  if (route_shadow_this_candidate) {
    ++routed_shadow_evals_;
  }
  return estimate;
}

Estimate MeasuredCriticalPathPolicy::measureCandidateWithTiming(
    const Target& target,
    MoveCandidate& candidate,
    const MoveType type,
    const bool route_shadow_this_candidate) const
{
  const sta::Pin* endpoint_pin = target.endpointPin(resizer_);
  if (route_shadow_this_candidate) {
    refreshRoutedTiming();
  } else if (useGlobalRouteBaseline()
             && envInt("RSZ_MEASURED_CRIT_GLOBAL_TARGET_ONLY", 1) != 0) {
    updateEvaluationTiming();
  }
  const float before_tns = sta_->totalNegativeSlack(max_);
  const float before_wns = sta_->worstSlack(max_);
  const float before_endpoint = endpointSlack(endpoint_pin);
  const int top_endpoint_count
      = std::max(0, envInt("RSZ_MEASURED_CRIT_TOP_TNS_ENDPOINTS", 0));
  const float before_top_tns
      = top_endpoint_count > 0 ? criticalEndpointTns(top_endpoint_count)
                               : 0.0f;
  CellCostMetrics before_cost;
  if (type == MoveType::kSizeUp || type == MoveType::kVtSwap
      || type == MoveType::kClone || type == MoveType::kSplitLoad
      || type == MoveType::kBuffer || type == MoveType::kUnbuffer) {
    before_cost = costMetrics(target.inst(resizer_));
  }
  RerouteCandidate* reroute_candidate = nullptr;
  bool reroute_was_res_aware = false;
  if (type == MoveType::kReroute) {
    reroute_candidate = dynamic_cast<RerouteCandidate*>(&candidate);
    if (reroute_candidate != nullptr) {
      reroute_was_res_aware = reroute_candidate->isResistanceAware();
    }
  }

  odb::dbDatabase::beginEco(resizer_.block());
  const MoveResult result = candidate.apply();
  if (!result.accepted) {
    if (reroute_candidate != nullptr) {
      reroute_candidate->restoreResistanceAware(reroute_was_res_aware);
    }
    resizer_.initForJournalRestore();
    odb::dbDatabase::undoEco(resizer_.block());
    return {.legal = false, .score = 0.0f};
  }

  if (route_shadow_this_candidate) {
    refreshRoutedTiming();
  } else {
    updateEvaluationTiming();
  }
  const float after_tns = sta_->totalNegativeSlack(max_);
  const float after_wns = sta_->worstSlack(max_);
  const float after_endpoint = endpointSlack(endpoint_pin);
  const float after_top_tns
      = top_endpoint_count > 0 ? criticalEndpointTns(top_endpoint_count)
                               : 0.0f;
  CellCostMetrics after_cost;
  if (type == MoveType::kSizeUp || type == MoveType::kVtSwap
      || type == MoveType::kClone || type == MoveType::kSplitLoad) {
    after_cost = sumCostMetrics(result.touched_instances);
  } else if (type == MoveType::kBuffer) {
    after_cost = sumCostMetrics(result.touched_instances);
  }
  const int move_count = result.move_count;

  resizer_.initForJournalRestore();
  odb::dbDatabase::undoEco(resizer_.block());
  if (reroute_candidate != nullptr) {
    reroute_candidate->restoreResistanceAware(reroute_was_res_aware);
  }
  if (route_shadow_this_candidate) {
    refreshRoutedTiming();
  } else {
    updateEvaluationTiming();
  }

  const float tns_gain = after_tns - before_tns;
  const float wns_gain = after_wns - before_wns;
  const float endpoint_gain = after_endpoint - before_endpoint;
  const float top_tns_gain = after_top_tns - before_top_tns;

  const float min_tns_gain
      = envFloat("RSZ_MEASURED_CRIT_MIN_TNS_GAIN", 1.0e-13f);
  const float min_wns_gain
      = envFloat("RSZ_MEASURED_CRIT_MIN_WNS_GAIN", 0.5e-13f);
  const float min_endpoint_gain
      = envFloat("RSZ_MEASURED_CRIT_MIN_ENDPOINT_GAIN", 0.5e-13f);
  const bool tns_first = envInt("RSZ_MEASURED_CRIT_TNS_FIRST", 0) != 0;
  const bool allow_wns_only =
      envInt("RSZ_MEASURED_CRIT_ALLOW_WNS_ONLY", tns_first ? 0 : 1) != 0;
  const bool allow_endpoint_only =
      envInt("RSZ_MEASURED_CRIT_ALLOW_ENDPOINT_ONLY", tns_first ? 0 : 1) != 0;
  const bool globally_useful =
      tns_gain >= min_tns_gain
      || (top_endpoint_count > 0 && top_tns_gain >= min_tns_gain)
      || (allow_wns_only && wns_gain >= min_wns_gain)
      || (allow_endpoint_only && endpoint_gain >= min_endpoint_gain);
  if (!globally_useful) {
    return {.legal = false, .score = tns_gain};
  }

  const float max_tns_damage
      = envFloat("RSZ_MEASURED_CRIT_MAX_TNS_DAMAGE", 0.0f);
  const float max_wns_damage
      = envFloat("RSZ_MEASURED_CRIT_MAX_WNS_DAMAGE", 0.0f);
  if (tns_gain < -max_tns_damage || wns_gain < -max_wns_damage) {
    return {.legal = false, .score = tns_gain + wns_gain};
  }

  if (envInt("RSZ_MEASURED_CRIT_POWER_GUARD",
             envInt("RSZ_TIMING_POWER_AWARE", 0)) != 0
      && (type == MoveType::kSizeUp || type == MoveType::kVtSwap
          || type == MoveType::kBuffer || type == MoveType::kSplitLoad
          || type == MoveType::kClone)) {
    const float leakage_growth
        = growthRatio(before_cost.leakage, after_cost.leakage, 1.0e-9f);
    const float cap_growth
        = growthRatio(before_cost.input_cap, after_cost.input_cap, 1.0e-15f);
    const float max_leakage_growth = envFloat(
        "RSZ_MEASURED_CRIT_MAX_LEAKAGE_GROWTH",
        type == MoveType::kVtSwap ? 8.0f : 5.0f);
    const float max_cap_growth
        = envFloat("RSZ_MEASURED_CRIT_MAX_CAP_GROWTH",
                   type == MoveType::kBuffer ? 8.0f : 4.0f);
    const float strong_gain = std::max(tns_gain, top_tns_gain);
    const float bypass_gain
        = envFloat("RSZ_MEASURED_CRIT_POWER_GUARD_BYPASS_TNS_GAIN",
                   1.5e-12f);
    if ((leakage_growth > max_leakage_growth
         || cap_growth > max_cap_growth)
        && strong_gain < bypass_gain) {
      return {.legal = false,
              .score = strong_gain - leakage_growth * 1.0e-13f
                       - cap_growth * 0.5e-13f};
    }
  }

  const float wns_weight = envFloat("RSZ_MEASURED_CRIT_WNS_WEIGHT", 0.45f);
  const float endpoint_weight
      = envFloat("RSZ_MEASURED_CRIT_ENDPOINT_WEIGHT", 0.20f);
  const float tns_weight = envFloat("RSZ_MEASURED_CRIT_TNS_WEIGHT",
                                    tns_first ? 1.75f : 1.0f);
  const float top_tns_weight
      = top_endpoint_count > 0
            ? envFloat("RSZ_MEASURED_CRIT_TOP_TNS_WEIGHT", 0.65f)
            : 0.0f;
  const float score =
      tns_weight * tns_gain + wns_weight * positiveGain(0.0f, wns_gain)
      + top_tns_weight * positiveGain(0.0f, top_tns_gain)
      + endpoint_weight * positiveGain(0.0f, endpoint_gain)
      - movePenalty(type)
      - measuredCostPenalty(type, before_cost, after_cost, move_count);
  return {.legal = score > 0.0f, .score = score};
}

bool MeasuredCriticalPathPolicy::commitMeasuredCandidate(CandidateScore& best)
{
  MoveResult result = committer_.commit(*best.candidate);
  if (!result.accepted) {
    return false;
  }
  if (useRouteShadow()) {
    refreshRoutedTiming();
  } else if (useGlobalRouteBaseline() && shouldRefreshGlobalRouteBaseline()) {
    refreshGlobalRouteBaseline(false);
  } else {
    updateEvaluationTiming();
  }
  committer_.acceptPendingMoves();
  ++committed_moves_;
  recordCommitted(best.type);
  logger_->info(utl::RSZ,
                kMsgPolicyCommittedMoves,
                "MeasuredCriticalPathPolicy committed {} {} moves.",
                committed_moves_,
                moveName(best.type));
  return true;
}

bool MeasuredCriticalPathPolicy::useRouteShadow() const
{
  if (useGlobalRouteBaseline()) {
    return false;
  }
  if (envInt("RSZ_MEASURED_CRIT_ROUTE_SHADOW", 0) == 0) {
    return false;
  }
  grt::GlobalRouter* global_router = resizer_.globalRouter();
  return global_router != nullptr && global_router->haveRoutes();
}

bool MeasuredCriticalPathPolicy::useGlobalRouteBaseline() const
{
  if (envInt("RSZ_MEASURED_CRIT_GLOBAL_ROUTE_BASELINE", 0) == 0) {
    return false;
  }
  return resizer_.globalRouter() != nullptr
         && resizer_.estimateParasitics() != nullptr;
}

bool MeasuredCriticalPathPolicy::shouldRefreshGlobalRouteBaseline() const
{
  const int interval = envInt("RSZ_MEASURED_CRIT_GLOBAL_ROUTE_REFRESH", 24);
  const int next_committed_count = committed_moves_ + 1;
  return interval > 0 && next_committed_count > 0
         && next_committed_count % interval == 0;
}

void MeasuredCriticalPathPolicy::refreshRoutedTiming() const
{
  grt::GlobalRouter* global_router = resizer_.globalRouter();
  est::EstimateParasitics* estimate_parasitics = resizer_.estimateParasitics();
  if (global_router == nullptr || estimate_parasitics == nullptr) {
    resizer_.updateParasiticsAndTiming();
    return;
  }
  global_router->globalRoute(true);

  const bool incremental_enabled
      = estimate_parasitics->isIncrementalParasiticsEnabled();
  if (incremental_enabled) {
    estimate_parasitics->setIncrementalParasiticsEnabled(false);
  }
  estimate_parasitics->estimateParasitics(est::ParasiticsSrc::kGlobalRouting);
  if (incremental_enabled) {
    estimate_parasitics->setIncrementalParasiticsEnabled(true);
  }
  sta_->findRequireds();
}

void MeasuredCriticalPathPolicy::refreshGlobalRouteBaseline(
    const bool save_guides) const
{
  grt::GlobalRouter* global_router = resizer_.globalRouter();
  est::EstimateParasitics* estimate_parasitics = resizer_.estimateParasitics();
  if (global_router == nullptr || estimate_parasitics == nullptr) {
    resizer_.updateParasiticsAndTiming();
    return;
  }

  global_router->globalRoute(save_guides);

  const bool incremental_enabled
      = estimate_parasitics->isIncrementalParasiticsEnabled();
  if (incremental_enabled) {
    estimate_parasitics->setIncrementalParasiticsEnabled(false);
  }
  estimate_parasitics->estimateParasitics(est::ParasiticsSrc::kGlobalRouting);
  if (incremental_enabled) {
    estimate_parasitics->setIncrementalParasiticsEnabled(true);
  }
  sta_->findRequireds();
}

void MeasuredCriticalPathPolicy::updateEvaluationTiming() const
{
  if (!useGlobalRouteBaseline()) {
    resizer_.updateParasiticsAndTiming();
    return;
  }

  est::EstimateParasitics* estimate_parasitics = resizer_.estimateParasitics();
  if (estimate_parasitics == nullptr) {
    resizer_.updateParasiticsAndTiming();
    return;
  }

  const bool incremental_enabled
      = estimate_parasitics->isIncrementalParasiticsEnabled();
  if (incremental_enabled) {
    estimate_parasitics->setIncrementalParasiticsEnabled(false);
  }
  const est::ParasiticsSrc src
      = envInt("RSZ_MEASURED_CRIT_GLOBAL_CANDIDATE_EVAL", 0) != 0
            ? est::ParasiticsSrc::kGlobalRouting
            : est::ParasiticsSrc::kPlacement;
  // Re-running global route while iterating candidates can invalidate STA Path
  // handles stored in the current Target.  Keep candidate scoring route-aware
  // by using the current global-routing parasitics, and require an explicit
  // opt-in for the slower/unsafe per-candidate reroute mode.
  if (src == est::ParasiticsSrc::kGlobalRouting
      && envInt("RSZ_MEASURED_CRIT_GLOBAL_CANDIDATE_ROUTE", 0) != 0
      && envInt("RSZ_MEASURED_CRIT_UNSAFE_CANDIDATE_REROUTE", 0) != 0) {
    grt::GlobalRouter* global_router = resizer_.globalRouter();
    if (global_router != nullptr) {
      global_router->globalRoute(false);
    }
  }
  estimate_parasitics->estimateParasitics(src);
  if (incremental_enabled) {
    estimate_parasitics->setIncrementalParasiticsEnabled(true);
  }
  sta_->findRequireds();
}

float MeasuredCriticalPathPolicy::endpointSlack(
    const sta::Pin* endpoint_pin) const
{
  if (endpoint_pin == nullptr) {
    return 0.0f;
  }
  sta::Vertex* endpoint = resizer_.graph()->pinLoadVertex(endpoint_pin);
  if (endpoint == nullptr) {
    return 0.0f;
  }
  return sta_->slack(endpoint, max_);
}

float MeasuredCriticalPathPolicy::criticalEndpointTns(const int max_count) const
{
  if (max_count <= 0) {
    return 0.0f;
  }
  target_collector_->init(config_.setup_slack_margin);
  target_collector_->collectViolatingEndpoints();

  std::vector<sta::Slack> slacks;
  slacks.reserve(target_collector_->getViolatingEndpoints().size());
  for (const auto& [endpoint_pin, endpoint_slack] :
       target_collector_->getViolatingEndpoints()) {
    sta::Vertex* endpoint = resizer_.graph()->pinLoadVertex(endpoint_pin);
    if (endpoint == nullptr) {
      continue;
    }
    if (sta::fuzzyLess(endpoint_slack, config_.setup_slack_margin)) {
      slacks.push_back(endpoint_slack);
    }
  }
  std::ranges::sort(slacks);

  float tns = 0.0f;
  const int count = std::min<int>(max_count, slacks.size());
  for (int i = 0; i < count; ++i) {
    tns += std::min(0.0f,
                    static_cast<float>(slacks[i] - config_.setup_slack_margin));
  }
  return tns;
}

MeasuredCriticalPathPolicy::CellCostMetrics
MeasuredCriticalPathPolicy::costMetrics(sta::Instance* inst) const
{
  CellCostMetrics metrics;
  if (inst == nullptr) {
    return metrics;
  }
  sta::LibertyCell* cell = resizer_.network()->libertyCell(inst);
  if (cell == nullptr) {
    return metrics;
  }
  odb::dbMaster* master = resizer_.dbNetwork()->staToDb(cell);
  metrics.area = master != nullptr ? static_cast<float>(master->getArea())
                                   : cell->area();
  metrics.leakage = resizer_.cellLeakage(cell).value_or(0.0f);

  sta::LibertyCellPortIterator port_iter(cell);
  while (port_iter.hasNext()) {
    sta::LibertyPort* port = port_iter.next();
    if (port != nullptr && port->direction()->isAnyInput()) {
      metrics.input_cap += port->capacitance();
    }
  }
  return metrics;
}

MeasuredCriticalPathPolicy::CellCostMetrics
MeasuredCriticalPathPolicy::sumCostMetrics(
    const std::vector<sta::Instance*>& instances) const
{
  CellCostMetrics sum;
  for (sta::Instance* inst : instances) {
    CellCostMetrics metrics = costMetrics(inst);
    sum.area += metrics.area;
    sum.leakage += metrics.leakage;
    sum.input_cap += metrics.input_cap;
  }
  return sum;
}

float MeasuredCriticalPathPolicy::measuredCostPenalty(
    const MoveType type,
    const CellCostMetrics& before,
    const CellCostMetrics& after,
    const int move_count) const
{
  if (envInt("RSZ_MEASURED_CRIT_COST_AWARE",
             envInt("RSZ_TIMING_POWER_AWARE", 0)) == 0) {
    return 0.0f;
  }

  const float area_ratio = growthRatio(before.area, after.area, 1.0f);
  const float leakage_ratio = growthRatio(before.leakage, after.leakage, 1.0e-9f);
  const float cap_ratio = growthRatio(before.input_cap, after.input_cap, 1.0e-15f);
  const float area_penalty
      = envFloat("RSZ_MEASURED_CRIT_AREA_PENALTY", 0.18e-13f);
  const float leakage_penalty
      = envFloat("RSZ_MEASURED_CRIT_LEAK_PENALTY", 0.75e-13f);
  const float cap_penalty
      = envFloat("RSZ_MEASURED_CRIT_CAP_PENALTY", 0.45e-13f);
  const float insert_penalty
      = envFloat("RSZ_MEASURED_CRIT_INSERT_PENALTY", 0.30e-13f);
  const float buffer_insert_penalty
      = envFloat("RSZ_MEASURED_CRIT_BUFFER_INSERT_PENALTY", 0.70e-13f);

  float penalty = area_penalty * std::min(area_ratio, 8.0f)
                  + leakage_penalty * std::min(leakage_ratio, 8.0f)
                  + cap_penalty * std::min(cap_ratio, 8.0f);
  if (type == MoveType::kBuffer) {
    penalty += buffer_insert_penalty * std::max(1, move_count);
  } else if (type == MoveType::kSplitLoad || type == MoveType::kClone) {
    penalty += insert_penalty * std::max(1, move_count);
  }

  const bool low_cost_replacement =
      (type == MoveType::kSizeUp || type == MoveType::kVtSwap)
      && after.area <= before.area * 1.05f
      && after.input_cap <= before.input_cap * 1.10f
      && after.leakage <= before.leakage * 1.25f;
  if (low_cost_replacement) {
    penalty -= envFloat("RSZ_MEASURED_CRIT_LOW_COST_BONUS", 0.18e-13f);
  }
  if (type == MoveType::kUnbuffer) {
    const float area_drop = growthRatio(after.area, before.area, 1.0f);
    const float leakage_drop = growthRatio(after.leakage, before.leakage, 1.0e-9f);
    const float cap_drop = growthRatio(after.input_cap, before.input_cap, 1.0e-15f);
    const float unbuffer_bonus =
        envFloat("RSZ_MEASURED_CRIT_UNBUFFER_BONUS", 0.35e-13f);
    penalty -= unbuffer_bonus
               * std::min(3.0f, 1.0f + area_drop + leakage_drop + cap_drop);
  }
  return std::max(0.0f, penalty);
}

float MeasuredCriticalPathPolicy::movePenalty(const MoveType type) const
{
  switch (type) {
    case MoveType::kSizeUp:
      return envFloat("RSZ_MEASURED_CRIT_SIZEUP_PENALTY", 0.25e-13f);
    case MoveType::kVtSwap:
      return envFloat("RSZ_MEASURED_CRIT_VT_PENALTY", 1.50e-13f);
    case MoveType::kBuffer:
      return envFloat("RSZ_MEASURED_CRIT_BUFFER_PENALTY", 0.75e-13f);
    case MoveType::kSplitLoad:
      return envFloat("RSZ_MEASURED_CRIT_SPLIT_PENALTY", 0.55e-13f);
    case MoveType::kClone:
      return envFloat("RSZ_MEASURED_CRIT_CLONE_PENALTY", 0.70e-13f);
    case MoveType::kUnbuffer:
      return envFloat("RSZ_MEASURED_CRIT_UNBUFFER_PENALTY", -0.10e-13f);
    case MoveType::kReroute:
      return envFloat("RSZ_MEASURED_CRIT_REROUTE_PENALTY", 0.10e-13f);
    default:
      return envFloat("RSZ_MEASURED_CRIT_OTHER_PENALTY", 1.0e-13f);
  }
}

void MeasuredCriticalPathPolicy::recordApplicable(const MoveType type)
{
  const int index = static_cast<int>(type);
  if (index >= 0 && index < static_cast<int>(type_stats_.size())) {
    ++type_stats_[index].applicable;
  }
}

void MeasuredCriticalPathPolicy::recordGenerated(const MoveType type,
                                                 const int count)
{
  const int index = static_cast<int>(type);
  if (index >= 0 && index < static_cast<int>(type_stats_.size())) {
    type_stats_[index].generated += count;
  }
}

void MeasuredCriticalPathPolicy::recordLegal(const MoveType type,
                                             const float score)
{
  const int index = static_cast<int>(type);
  if (index >= 0 && index < static_cast<int>(type_stats_.size())) {
    TypeStats& stats = type_stats_[index];
    ++stats.legal;
    stats.best_score = std::max(stats.best_score, score);
  }
}

void MeasuredCriticalPathPolicy::recordCommitted(const MoveType type)
{
  const int index = static_cast<int>(type);
  if (index >= 0 && index < static_cast<int>(type_stats_.size())) {
    ++type_stats_[index].committed;
  }
}

void MeasuredCriticalPathPolicy::reportCandidateStats() const
{
  for (int index = 0; index < static_cast<int>(MoveType::kCount); ++index) {
    const TypeStats& stats = type_stats_[index];
    if (stats.applicable == 0 && stats.generated == 0 && stats.legal == 0
        && stats.committed == 0) {
      continue;
    }
    const MoveType type = static_cast<MoveType>(index);
    logger_->info(utl::RSZ,
                  2025,
                  "MeasuredCriticalPathPolicy candidate stats {}: applicable={} generated={} legal={} committed={} best_score={:.3e}.",
                  moveName(type),
                  stats.applicable,
                  stats.generated,
                  stats.legal,
                  stats.committed,
                  stats.best_score);
  }
}

void MeasuredCriticalPathPolicy::finishRun(const bool result)
{
  reportCandidateStats();
  markRunComplete(result);
}

}  // namespace rsz
