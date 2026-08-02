// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "SplitLoadCandidate.hh"

#include <algorithm>
#include <cstdlib>
#include <cmath>
#include <memory>
#include <utility>

#include "MoveCandidate.hh"
#include "OptimizerTypes.hh"
#include "db_sta/dbSta.hh"
#include "odb/geom.h"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

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

bool envFlag(const char* name, const bool default_value = false)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) == "1" || std::string(value) == "true"
         || std::string(value) == "TRUE" || std::string(value) == "yes";
}

}  // namespace

SplitLoadCandidate::SplitLoadCandidate(Resizer& resizer,
                                       const Target& target,
                                       sta::Net* drvr_net,
                                       sta::LibertyCell* buffer_cell,
                                       const odb::Point& drvr_loc,
                                       std::unique_ptr<sta::PinSet> load_pins)
    : MoveCandidate(resizer, target),
      drvr_net_(drvr_net),
      buffer_cell_(buffer_cell),
      drvr_loc_(drvr_loc),
      load_pins_(std::move(load_pins))
{
}

SplitLoadCandidate::~SplitLoadCandidate() = default;

Estimate SplitLoadCandidate::estimate()
{
  const int fanout = std::max(1, target_.fanout);
  const int moved_count = load_pins_ != nullptr ? load_pins_->size() : 0;
  const bool allow_low_fanout
      = envFlag("RSZ_SPLIT_LOAD_ALLOW_LOW_FANOUT", false);
  const bool critical_branch_isolation
      = allow_low_fanout && target_.slack <= -9.0e-11f && moved_count >= 1
        && fanout >= 2;
  const bool conventional_split = moved_count >= 2 && fanout >= 4;
  if ((!critical_branch_isolation && !conventional_split)
      || target_.slack > -9.0e-11f) {
    return {.legal = false, .score = 0.0f};
  }

  const float criticality
      = std::min(5.0f, std::max(0.0f, -static_cast<float>(target_.slack)
                                           / 1.0e-10f));
  const float relief_ratio = static_cast<float>(moved_count) / fanout;
  const float fanout_scale = std::sqrt(static_cast<float>(fanout));
  const float timing_gain
      = (0.45e-11f + 0.45e-11f * criticality) * relief_ratio * fanout_scale;

  const float buffer_leakage = leakageOf(resizer_, buffer_cell_);
  const float leakage_cost = 3.0e-12f * buffer_leakage / positiveOrOne(1.0e-9f);
  const float area_cost
      = 0.75e-12f * (buffer_cell_ != nullptr ? buffer_cell_->area() : 0.0f)
        / positiveOrOne(1.0f);
  const float low_fanout_penalty
      = fanout < 4 ? 0.15e-11f : (fanout < 6 ? 0.45e-11f : 0.0f);
  const float score = timing_gain - leakage_cost - area_cost - low_fanout_penalty;
  return {.legal = score > 0.0f, .score = score};
}

MoveResult SplitLoadCandidate::apply()
{
  return applySplitBuffer();
}

int SplitLoadCandidate::resizeInsertedBuffer(sta::Instance* buffer) const
{
  sta::LibertyPort *input, *output;
  buffer_cell_->bufferPorts(input, output);
  sta::Pin* buffer_out_pin = resizer_.network()->findPin(buffer, output);
  return resizer_.resizeToTargetSlew(buffer_out_pin);
}

void SplitLoadCandidate::invalidateAffectedParasitics(
    sta::Instance* buffer) const
{
  sta::LibertyPort *input, *output;
  buffer_cell_->bufferPorts(input, output);
  sta::Pin* buffer_out_pin = resizer_.network()->findPin(buffer, output);
  resizer_.invalidateParasitics(drvr_net_);
  resizer_.invalidateParasitics(resizer_.network()->net(buffer_out_pin));
}

MoveResult SplitLoadCandidate::applySplitBuffer()
{
  sta::Instance* buffer
      = resizer_.insertBufferBeforeLoads(drvr_net_,
                                         load_pins_.get(),
                                         buffer_cell_,
                                         &drvr_loc_,
                                         "split",
                                         nullptr,
                                         odb::dbNameUniquifyType::IF_NEEDED);
  if (buffer == nullptr) {
    debugPrint(resizer_.logger(),
               RSZ,
               "split_load_move",
               2,
               "REJECT SplitLoadMove {}: Couldn't insert buffer",
               resizer_.network()->pathName(target_.driver_pin));
    return rejectedMove();
  }

  debugPrint(resizer_.logger(),
             RSZ,
             "split_load_move",
             1,
             "ACCEPT SplitLoadMove {}: Inserted buffer {}",
             resizer_.network()->pathName(target_.driver_pin),
             resizer_.network()->pathName(buffer));

  static_cast<void>(resizeInsertedBuffer(buffer));
  invalidateAffectedParasitics(buffer);

  return {
      .accepted = true,
      .type = MoveType::kSplitLoad,
      .move_count = 1,
      .touched_instances = {buffer},
  };
}

}  // namespace rsz
