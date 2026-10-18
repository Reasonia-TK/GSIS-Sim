#include "core/timer.hpp"

#include <algorithm>
#include <format>

namespace gsis {

void TimingTable::add(std::string_view name, double seconds) {
  auto it = std::find_if(entries_.begin(), entries_.end(),
                         [&](const Entry& e) { return e.name == name; });
  if (it == entries_.end()) {
    entries_.push_back(Entry{std::string(name), seconds, 1});
  } else {
    it->seconds += seconds;
    ++it->calls;
  }
}

double TimingTable::total(std::string_view name) const {
  for (const Entry& e : entries_) {
    if (e.name == name) {
      return e.seconds;
    }
  }
  return 0.0;
}

std::string TimingTable::format() const {
  double sum = 0.0;
  for (const Entry& e : entries_) {
    sum += e.seconds;
  }
  std::string out;
  for (const Entry& e : entries_) {
    const double share = sum > 0.0 ? 100.0 * e.seconds / sum : 0.0;
    out += std::format("  {:<24} {:12.4f} s {:8} 回 {:6.1f} %\n", e.name, e.seconds, e.calls,
                       share);
  }
  return out;
}

}  // namespace gsis
