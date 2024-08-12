# Copyright (c) 2023 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import unittest

import numpy as np

import paddle
from paddle import base
from paddle.base import core
from paddle.vision.models import resnet50

SEED = 2020
base_lr = 0.001
momentum_rate = 0.9
l2_decay = 1e-4
batch_size = 2
epoch_num = 1

# In V100, 16G, CUDA 11.2, the results are as follows:
# DY2ST_PRIM_GT = [
#     5.8473358154296875,
#     8.354944229125977,
#     5.098367691040039,
#     8.533346176147461,
#     8.179085731506348,
#     7.285282135009766,
#     9.824585914611816,
#     8.56928825378418,
#     8.539499282836914,
#     10.256929397583008,
# ]

# note: Version 2.0 momentum is fused to OP when L2Decay is available, and the results are different from the base version.
# The results in ci as as follows:
DY2ST_PRIM_GT = [
    5.847333908081055,
    8.368712425231934,
    4.989010334014893,
    8.523179054260254,
    7.997398376464844,
    7.601831436157227,
    9.777579307556152,
    8.428393363952637,
    8.581992149353027,
    10.313587188720703,
]

# IN V100, 16G, CUDA 12.0, the results are as follows:
DY2ST_PRIM_GT_CUDA12 = [
    5.847333908081055,
    8.368700981140137,
    4.989815711975098,
    8.65414810180664,
    7.947117805480957,
    7.329026222229004,
    9.768604278564453,
    8.573783874511719,
    8.44187068939209,
    10.309272766113281,
]

if core.is_compiled_with_cuda():
    paddle.set_flags({'FLAGS_cudnn_deterministic': True})


def reader_decorator(reader):
    def __reader__():
        for item in reader():
            img = np.array(item[0]).astype('float32').reshape(3, 224, 224)
            label = np.array(item[1]).astype('int64').reshape(1)
            yield img, label

    return __reader__


class TransedFlowerDataSet(paddle.io.Dataset):
    def __init__(self, flower_data, length):
        self.img = []
        self.label = []
        self.flower_data = flower_data()
        self._generate(length)

    def _generate(self, length):
        for i, data in enumerate(self.flower_data):
            if i >= length:
                break
            self.img.append(data[0])
            self.label.append(data[1])

    def __getitem__(self, idx):
        return self.img[idx], self.label[idx]

    def __len__(self):
        return len(self.img)


def optimizer_setting(parameter_list=None):
    optimizer = paddle.optimizer.Momentum(
        learning_rate=base_lr,
        momentum=momentum_rate,
        weight_decay=paddle.regularizer.L2Decay(l2_decay),
        parameters=parameter_list,
    )

    return optimizer


def run(model, data_loader, optimizer, mode):
    if mode == 'train':
        model.train()
        end_step = 9
    elif mode == 'eval':
        model.eval()
        end_step = 1

    for epoch in range(epoch_num):
        total_acc1 = 0.0
        total_acc5 = 0.0
        total_sample = 0
        losses = []

        for batch_id, data in enumerate(data_loader()):
            start_time = time.time()
            img, label = data

            pred = model(img)
            avg_loss = paddle.nn.functional.cross_entropy(
                input=pred,
                label=label,
                soft_label=False,
                reduction='mean',
                use_softmax=True,
            )

            acc_top1 = paddle.static.accuracy(input=pred, label=label, k=1)
            acc_top5 = paddle.static.accuracy(input=pred, label=label, k=5)

            if mode == 'train':
                avg_loss.backward()
                optimizer.minimize(avg_loss)
                model.clear_gradients()

            total_acc1 += acc_top1
            total_acc5 += acc_top5
            total_sample += 1
            losses.append(avg_loss.numpy().item())

            end_time = time.time()
            print(
                "[%s]epoch %d | batch step %d, loss %0.8f, acc1 %0.3f, acc5 %0.3f, time %f"
                % (
                    mode,
                    epoch,
                    batch_id,
                    avg_loss,
                    total_acc1.numpy() / total_sample,
                    total_acc5.numpy() / total_sample,
                    end_time - start_time,
                )
            )
            if batch_id >= end_step:
                break
    print(losses)
    return losses


def train(to_static, enable_prim, enable_cinn):
    if core.is_compiled_with_cuda():
        paddle.set_device('gpu')
    else:
        paddle.set_device('cpu')
    np.random.seed(SEED)
    paddle.seed(SEED)
    paddle.framework.random._manual_program_seed(SEED)
    base.core._set_prim_all_enabled(enable_prim)

    dataset = TransedFlowerDataSet(
        reader_decorator(paddle.dataset.flowers.train(use_xmap=False)),
        batch_size * (10 + 1),
    )
    data_loader = paddle.io.DataLoader(
        dataset, batch_size=batch_size, drop_last=True
    )

    resnet = resnet50(False)
    if to_static:
        build_strategy = paddle.static.BuildStrategy()
        if enable_cinn:
            build_strategy.build_cinn_pass = True
        resnet = paddle.jit.to_static(
            resnet, build_strategy=build_strategy, full_graph=True
        )
    optimizer = optimizer_setting(parameter_list=resnet.parameters())

    train_losses = run(resnet, data_loader, optimizer, 'train')
    if to_static and enable_prim and enable_cinn:
        eval_losses = run(resnet, data_loader, optimizer, 'eval')
    return train_losses


class TestResnet(unittest.TestCase):
    @unittest.skipIf(
        not (paddle.is_compiled_with_cinn() and paddle.is_compiled_with_cuda()),
        "paddle is not compiled with CINN and CUDA",
    )
    def test_prim(self):
        dy2st_prim = train(to_static=True, enable_prim=True, enable_cinn=False)
        standard_prim = DY2ST_PRIM_GT
        if paddle.version.cuda() == "12.0":
            standard_prim = DY2ST_PRIM_GT_CUDA12
        np.testing.assert_allclose(dy2st_prim, standard_prim, rtol=1e-5)


if __name__ == '__main__':
    unittest.main()
