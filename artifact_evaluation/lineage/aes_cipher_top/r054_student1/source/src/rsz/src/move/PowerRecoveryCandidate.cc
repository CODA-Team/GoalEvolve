// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "PowerRecoveryCandidate.hh"

#include "MoveCandidate.hh"
#include "OptimizerTypes.hh"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"
#include "sta/NetworkClass.hh"
#include "utl/Logger.h"

namespace rsz {

using utl::RSZ;

const char* powerRecoveryMoveKindName(const PowerRecoveryMoveKind kind)
{
  switch (kind) {
    case PowerRecoveryMoveKind::kSizeDown:
      return "size_down";
    case PowerRecoveryMoveKind::kVtRecover:
      return "vt_recover";
    case PowerRecoveryMoveKind::kSizeDownAndVtRecover:
      return "size_down_and_vt_recover";
    case PowerRecoveryMoveKind::kRemoveBuffer:
      return "remove_buffer";
  }
  return "unknown";
}

PowerRecoveryCandidate::PowerRecoveryCandidate(
    Resizer& resizer,
    const Target& target,
    sta::Instance* inst,
    sta::Pin* output_pin,
    sta::LibertyCell* current_cell,
    sta::LibertyCell* candidate_cell,
    const PowerRecoveryMoveKind kind,
    const float score)
    : MoveCandidate(resizer, target),
      inst_(inst),
      output_pin_(output_pin),
      current_cell_(current_cell),
      candidate_cell_(candidate_cell),
      kind_(kind),
      score_(score)
{
}

MoveType PowerRecoveryCandidate::type() const
{
  if (kind_ == PowerRecoveryMoveKind::kRemoveBuffer) {
    return MoveType::kUnbuffer;
  }
  return kind_ == PowerRecoveryMoveKind::kVtRecover ? MoveType::kVtSwap
                                                    : MoveType::kSizeDown;
}

std::string PowerRecoveryCandidate::logName() const
{
  return resizer_.network()->pathName(inst_);
}

MoveResult PowerRecoveryCandidate::apply()
{
  if (kind_ == PowerRecoveryMoveKind::kRemoveBuffer) {
    const std::string removed_inst_name = logName();
    if (!resizer_.removeBuffer(inst_)) {
      debugPrint(resizer_.logger(),
                 RSZ,
                 "power_recovery_plus",
                 1,
                 "POWER_RECOVERY_PLUS|reject_commit|inst={}|kind={}|from={}|"
                 "to=<deleted>|reason=remove_failed|score={:.6g}",
                 removed_inst_name,
                 powerRecoveryMoveKindName(kind_),
                 current_cell_ != nullptr ? current_cell_->name() : "<null>",
                 score_);
      return rejectedMove();
    }

    resizer_.logger()->info(
        RSZ,
        2304,
        "POWER_RECOVERY_PLUS|accept|inst={}|kind={}|from={}|to=<deleted>|"
        "score={:.6g}",
        removed_inst_name,
        powerRecoveryMoveKindName(kind_),
        current_cell_ != nullptr ? current_cell_->name() : "<null>",
        score_);

    return {
        .accepted = true,
        .type = type(),
        .move_count = 1,
        .touched_instances = {inst_},
    };
  }

  if (!resizer_.replacementPreservesMaxCap(inst_, candidate_cell_)) {
    debugPrint(resizer_.logger(),
               RSZ,
               "power_recovery_plus",
               1,
               "POWER_RECOVERY_PLUS|reject_commit|inst={}|kind={}|from={}|"
               "to={}|reason=max_cap|score={:.6g}",
               logName(),
               powerRecoveryMoveKindName(kind_),
               current_cell_->name(),
               candidate_cell_->name(),
               score_);
    return rejectedMove();
  }

  if (!resizer_.replaceCell(inst_, candidate_cell_)) {
    debugPrint(resizer_.logger(),
               RSZ,
               "power_recovery_plus",
               1,
               "POWER_RECOVERY_PLUS|reject_commit|inst={}|kind={}|from={}|"
               "to={}|reason=replace_failed|score={:.6g}",
               logName(),
               powerRecoveryMoveKindName(kind_),
               current_cell_->name(),
               candidate_cell_->name(),
               score_);
    return rejectedMove();
  }

  resizer_.logger()->info(
      RSZ,
      2301,
      "POWER_RECOVERY_PLUS|accept|inst={}|kind={}|from={}|to={}|score={:.6g}",
      logName(),
      powerRecoveryMoveKindName(kind_),
      current_cell_->name(),
      candidate_cell_->name(),
      score_);

  return {
      .accepted = true,
      .type = type(),
      .move_count = 1,
      .touched_instances = {inst_},
  };
}

}  // namespace rsz
