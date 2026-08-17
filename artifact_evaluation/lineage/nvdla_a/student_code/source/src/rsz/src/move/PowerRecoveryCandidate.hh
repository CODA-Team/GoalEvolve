// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#pragma once

#include <string>

#include "MoveCandidate.hh"
#include "OptimizerTypes.hh"

namespace sta {
class Instance;
class LibertyCell;
class Pin;
}  // namespace sta

namespace rsz {

enum class PowerRecoveryMoveKind
{
  kSizeDown,
  kVtRecover,
  kSizeDownAndVtRecover,
  kRemoveBuffer
};

class PowerRecoveryCandidate : public MoveCandidate
{
 public:
  PowerRecoveryCandidate(Resizer& resizer,
                         const Target& target,
                         sta::Instance* inst,
                         sta::Pin* output_pin,
                         sta::LibertyCell* current_cell,
                         sta::LibertyCell* candidate_cell,
                         PowerRecoveryMoveKind kind,
                         float score);

  MoveResult apply() override;
  MoveType type() const override;
  std::string logName() const;

 private:
  sta::Instance* inst_;
  sta::Pin* output_pin_;
  sta::LibertyCell* current_cell_;
  sta::LibertyCell* candidate_cell_;
  PowerRecoveryMoveKind kind_;
  float score_;
};

const char* powerRecoveryMoveKindName(PowerRecoveryMoveKind kind);

}  // namespace rsz
