// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026-2026, The OpenROAD Authors

#include "BufferGenerator.hh"

#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

#include "BufferCandidate.hh"
#include "MoveCandidate.hh"
#include "MoveGenerator.hh"
#include "OptimizerTypes.hh"
#include "rsz/Resizer.hh"
#include "sta/Liberty.hh"
#include "sta/Network.hh"

namespace rsz {

namespace {

constexpr int kRebufferMaxFanout = 64;
constexpr int kDrvUsefulFanout = 5;
constexpr float kDrvSlackRichFloor = 4.0e-11f;

bool envFlag(const char* name, const bool default_value = false)
{
  const char* value = std::getenv(name);
  if (value == nullptr) {
    return default_value;
  }
  return std::string(value) != "0";
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

bool weakLogicDriver(sta::LibertyCell* cell)
{
  if (cell == nullptr) {
    return false;
  }
  const std::string name = cell->name();
  return name.find("xp33") != std::string::npos
         || name.find("XP33") != std::string::npos
         || name.find("xp5") != std::string::npos
         || name.find("XP5") != std::string::npos;
}

} // namespace

BufferGenerator::BufferGenerator(const GeneratorContext &context)
    : MoveGenerator(context) {}

bool BufferGenerator::isApplicable(const Target &target) const {
  const bool low_benefit_slack_rich_net =
      target.fanout <= kDrvUsefulFanout && target.slack > kDrvSlackRichFloor;
  sta::Instance* inst = target.inst(resizer_);
  if (envFlag("RSZ_LOW_POWER_TIMING_BUFFER_GUARD", false)) {
    const int min_fanout = envInt("RSZ_BUFFER_LOW_POWER_MIN_FANOUT", 8);
    const float critical_slack =
        envFloat("RSZ_BUFFER_LOW_POWER_CRITICAL_SLACK", -3.2e-10f);
    sta::LibertyCell* cell = inst != nullptr
                                 ? resizer_.network()->libertyCell(inst)
                                 : nullptr;
    const bool structurally_needed =
        target.fanout >= min_fanout || target.slack <= critical_slack;
    if (!structurally_needed || (weakLogicDriver(cell) && target.fanout <= 6)) {
      return false;
    }
  }
  return MoveGenerator::isApplicable(target) && target.fanout > 1 &&
         target.fanout < kRebufferMaxFanout && !low_benefit_slack_rich_net &&
         !resizer_.drivesSequentialClockPin(inst) &&
         resizer_.okToBufferNet(target.driver_pin);
}

std::vector<std::unique_ptr<MoveCandidate>>
BufferGenerator::generate(const Target &target) {
  std::vector<std::unique_ptr<MoveCandidate>> candidates;
  candidates.push_back(
      std::make_unique<BufferCandidate>(resizer_, target, target.driver_pin));
  return candidates;
}

} // namespace rsz
