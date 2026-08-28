#pragma once

#include "continuous_auth/collector/config.hpp"

#include <atomic>
#include <cstddef>
#include <mutex>
#include <optional>
#include <utility>
#include <vector>

namespace continuous_auth::collector {

template <typename T> class BoundedBuffer {
public:
  BoundedBuffer(std::size_t capacity, OverloadPolicy policy)
      : slots_(capacity), policy_(policy) {
    if (capacity == 0) {
      throw std::invalid_argument("bounded buffer capacity must be positive");
    }
  }

  bool try_push(T value) noexcept {
    std::unique_lock<std::mutex> lock(mutex_, std::try_to_lock);
    if (!lock.owns_lock()) {
      dropped_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    if (size_ == slots_.size()) {
      dropped_.fetch_add(1, std::memory_order_relaxed);
      if (policy_ == OverloadPolicy::drop_newest) {
        return false;
      }
      slots_[tail_].reset();
      tail_ = (tail_ + 1) % slots_.size();
      --size_;
    }
    slots_[head_].emplace(std::move(value));
    head_ = (head_ + 1) % slots_.size();
    ++size_;
    const auto previous = high_water_.load(std::memory_order_relaxed);
    if (size_ > previous) {
      high_water_.store(size_, std::memory_order_relaxed);
    }
    return true;
  }

  std::optional<T> try_pop() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (size_ == 0) {
      return std::nullopt;
    }
    auto value = std::move(slots_[tail_]);
    slots_[tail_].reset();
    tail_ = (tail_ + 1) % slots_.size();
    --size_;
    return value;
  }

  std::uint64_t dropped() const noexcept {
    return dropped_.load(std::memory_order_relaxed);
  }

  std::size_t high_water() const noexcept {
    return high_water_.load(std::memory_order_relaxed);
  }

  std::size_t size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return size_;
  }

private:
  mutable std::mutex mutex_;
  std::vector<std::optional<T>> slots_;
  OverloadPolicy policy_;
  std::size_t head_{};
  std::size_t tail_{};
  std::size_t size_{};
  std::atomic<std::uint64_t> dropped_{};
  std::atomic<std::size_t> high_water_{};
};

}  // namespace continuous_auth::collector
