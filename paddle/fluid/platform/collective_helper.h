//   Copyright (c) 2018 PaddlePaddle Authors. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#pragma once

#include <map>
#include <memory>
#include <string>
#include <vector>

#include "paddle/fluid/framework/data_type.h"
#include "paddle/fluid/platform/device_context.h"
#include "paddle/fluid/platform/enforce.h"
#include "paddle/phi/backends/device_manager.h"
#include "paddle/phi/core/platform/collective_helper.h"
#include "paddle/utils/variant.h"

namespace paddle {
namespace platform {

#if defined(PADDLE_WITH_CUSTOM_DEVICE)
class XCCLComm {
 public:
  virtual int ring_id() const = 0;
  virtual int nranks() const = 0;
  virtual int rank() const = 0;
  virtual int device_id() const = 0;
  virtual phi::ccl::CCLComm comm() const = 0;
  virtual std::shared_ptr<phi::stream::Stream> stream() const = 0;
  virtual std::shared_ptr<phi::event::Event> compute_event() const = 0;
  virtual std::shared_ptr<phi::event::Event> comm_event() const = 0;
  virtual phi::CustomContext* dev_context() const = 0;
  virtual ~XCCLComm() = default;
};

// A singleton XCCL communicator context reserves communication ring ids
class XCCLCommContext {
 public:
  static XCCLCommContext& Instance(const std::string& device_type);
  static void Release();

  XCCLComm* CreateComm(phi::ccl::CCLRootId* nccl_id,
                       int nranks,
                       int rank,
                       int dev_id,
                       int ring_id = 0);

  void CreateAllXCCLComms(const std::vector<int>& dev_ids, int ring_id = 0);

  void CreateXCCLCommMultiTrainer(const std::vector<int>& dev_ids,
                                  phi::ccl::CCLRootId* xccl_id,
                                  int nranks,
                                  int rank,
                                  int ring_id);

  // a latter comm with the same dev_id and the same ring_id
  // will override the former
  XCCLComm* AssignXCCLComm(phi::ccl::CCLComm comm,
                           int nranks,
                           int rank,
                           int dev_id,
                           int ring_id = 0);

  // retrieve a communicator by the ring id in multiprocessing mode
  XCCLComm* Get(int ring_id) const {
    PADDLE_ENFORCE_GT(
        comm_map_.count(ring_id),
        0,
        common::errors::InvalidArgument(
            "Communicator in ring id %d has not been initialized.", ring_id));
    PADDLE_ENFORCE_EQ(comm_map_.at(ring_id).size(),
                      1,
                      common::errors::InvalidArgument(
                          "One device id should be specified to retrieve from "
                          "multiple communicators."));
    return comm_map_.at(ring_id).begin()->second.get();
  }

  int GetRingId(phi::ccl::CCLComm comm) const {
    for (const auto& pair : comm_map_) {
      for (const auto& p : pair.second) {
        if (p.second.get()->comm() == comm) {
          return pair.first;
        }
      }
    }
    return -1;
  }

  // retrieve a communicator by the ring id and the device id
  XCCLComm* Get(int ring_id, int dev_id) const {
    PADDLE_ENFORCE_GT(
        comm_map_.count(ring_id),
        0,
        common::errors::InvalidArgument(
            "Communicator of ring id %d has not been initialized.", ring_id));
    PADDLE_ENFORCE_GT(
        comm_map_.at(ring_id).count(dev_id),
        0,
        common::errors::InvalidArgument(
            "Communicator at device id %d has not been initialized in ring %d.",
            dev_id,
            ring_id));
    return comm_map_.at(ring_id).at(dev_id).get();
  }

  // retrieve a communicator by the ring id and place
  XCCLComm* Get(int ring_id, Place place) const {
    return Get(ring_id, place.device);
  }

 private:
  std::string device_type_;
  std::once_flag once_flag_;
  std::mutex comm_map_mutex_;
  // ring id to dev-XCCLComm
  std::map<int, std::map<int, std::unique_ptr<XCCLComm>>> comm_map_;

  void ReleaseXCCLComms();

  XCCLCommContext() = default;
  explicit XCCLCommContext(const std::string& device_type)
      : device_type_(device_type) {}
  DISABLE_COPY_AND_ASSIGN(XCCLCommContext);
};
#endif
}  // namespace platform
}  // namespace paddle
