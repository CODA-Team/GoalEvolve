// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "RerouteCandidate.hh"

#include <algorithm>
#include <cmath>
#include <vector>

#include "MoveCandidate.hh"
#include "est/EstimateParasitics.h"
#include "grt/GlobalRouter.h"
#include "odb/db.h"
#include "rsz/Resizer.hh"
#include "sta/Network.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

RerouteCandidate::RerouteCandidate(Resizer& resizer,
                                   const Target& target,
                                   sta::Pin* driver_pin,
                                   sta::Instance* driver_inst,
                                   odb::dbNet* db_net,
                                   const float current_resistance,
                                   const float estimated_resistance)
    : MoveCandidate(resizer, target),
      driver_pin_(driver_pin),
      driver_inst_(driver_inst),
      db_net_(db_net),
      current_resistance_(current_resistance),
      estimated_resistance_(estimated_resistance)
{
}

Estimate RerouteCandidate::estimate()
{
  const float resistance_gain = current_resistance_ - estimated_resistance_;
  if (resistance_gain <= 0.0f) {
    return {.legal = false, .score = resistance_gain};
  }
  const float criticality =
      std::clamp(-static_cast<float>(target_.slack) / 1.0e-10f, 0.25f, 8.0f);
  const float reduction_ratio =
      resistance_gain / std::max(current_resistance_, 1.0e-6f);
  return {.legal = true,
          .score = resistance_gain * (1.0f + criticality) *
                   (1.0f + 2.0f * reduction_ratio)};
}

MoveResult RerouteCandidate::apply()
{
  grt::GlobalRouter* global_router = resizer_.globalRouter();
  if (global_router->isNetResAware(db_net_)) {
    return rejectedMove();
  }

  global_router->setResistanceAware(true);
  global_router->addDirtyNet(db_net_);
  global_router->setNetIsResAware(db_net_, true);
  resizer_.estimateParasitics()->parasiticsInvalid(db_net_);

  debugPrint(resizer_.logger(),
             RSZ,
             "reroute_move",
             1,
             "ACCEPT RerouteMove {}: Rerouted net {} (resistance {} -> {} "
             "estimated)",
             resizer_.network()->pathName(driver_pin_),
             db_net_->getName(),
             current_resistance_,
             estimated_resistance_);
  return {
      .accepted = true,
      .type = MoveType::kReroute,
      .move_count = 1,
      .touched_instances = {driver_inst_},
  };
}

bool RerouteCandidate::isResistanceAware() const
{
  grt::GlobalRouter* global_router = resizer_.globalRouter();
  return global_router != nullptr && global_router->isNetResAware(db_net_);
}

void RerouteCandidate::restoreResistanceAware(
    const bool was_resistance_aware) const
{
  grt::GlobalRouter* global_router = resizer_.globalRouter();
  if (global_router == nullptr) {
    return;
  }
  global_router->setNetIsResAware(db_net_, was_resistance_aware);
  global_router->addDirtyNet(db_net_);
  if (resizer_.estimateParasitics() != nullptr) {
    resizer_.estimateParasitics()->parasiticsInvalid(db_net_);
  }
}

}  // namespace rsz
