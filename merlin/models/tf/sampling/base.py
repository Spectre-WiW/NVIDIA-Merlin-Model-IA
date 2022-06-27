#
# Copyright (c) 2021, NVIDIA CORPORATION.
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
#
import abc
from typing import List, Optional, Sequence, Union

import tensorflow as tf

from merlin.models.tf.blocks.core.base import Block
from merlin.models.tf.sampling.collection import ItemCollection
from merlin.models.tf.typing import TabularData
from merlin.models.utils.registry import Registry, RegistryMixin
from merlin.schema import Schema, Tags

negative_sampling_registry: Registry = Registry.class_registry("tf.negative_sampling")


class ItemSampler(Block, RegistryMixin["ItemSampler"], abc.ABC):
    ITEM_EMBEDDING_KEY = "__item_embedding__"
    registry = negative_sampling_registry

    def __init__(
        self,
        max_num_samples: Optional[int] = None,
        **kwargs,
    ):
        super(ItemSampler, self).__init__(**kwargs)
        self.set_max_num_samples(max_num_samples)

    def call(self, items: ItemCollection, training=False) -> ItemCollection:
        if training:
            self.add(items)
        items = self.sample()

        return items

    @abc.abstractmethod
    def add(self, items: ItemCollection):
        raise NotImplementedError()

    @abc.abstractmethod
    def sample(self) -> ItemCollection:
        raise NotImplementedError()

    def _check_inputs_batch_sizes(self, items: ItemCollection):
        embeddings_batch_size = tf.shape(items.ids)[0]
        for feat_name in items.metadata:
            metadata_feat_batch_size = tf.shape(items.metadata[feat_name])[0]

            tf.assert_equal(
                embeddings_batch_size,
                metadata_feat_batch_size,
                "The batch size (first dim) of embeddings "
                f"({int(embeddings_batch_size)}) and metadata "
                f"features ({int(metadata_feat_batch_size)}) must match.",
            )

    @property
    def required_features(self) -> List[str]:
        return []

    @property
    def max_num_samples(self) -> int:
        return self._max_num_samples

    def set_max_num_samples(self, value) -> None:
        self._max_num_samples = value


class NegativeSampling(Block):
    def __init__(self, *samplers: ItemSampler, **kwargs):
        super(NegativeSampling, self).__init__(**kwargs)


class AddRandomNegativesToBatch(Block):
    def __init__(self, schema: Schema, n_per_positive: int, seed: Optional[int] = None, **kwargs):
        super(AddRandomNegativesToBatch, self).__init__(**kwargs)
        self.n_per_positive = n_per_positive
        self.schema = schema.select_by_tag(Tags.ITEM)
        self.seed = seed

    def call(self, inputs: TabularData):
        # 1. Select item-features -> ItemCollection
        batch_size = list(inputs.values())[0].shape[0]
        items = ItemCollection.from_features(self.schema, inputs)

        # 2. Sample `n_per_positive * batch_size` items at random
        sampled_ids = self.sample_ids(batch_size, items)

        # 3. Loop through all features:
        #   - For item-feature: append from item-collection
        #   - For user-feature: repeat `n_per_positive` times
        item_cols = self.schema.column_names
        outputs = {}
        for name, val in inputs.items():
            if name in item_cols:
                negatives = tf.gather(val, sampled_ids)
                outputs[name] = tf.concat([val, negatives], axis=0)
            else:
                outputs[name] = tf.repeat(val, self.n_per_positive + 1, axis=0)

        return outputs

    def sample_ids(self, batch_size: int, items: ItemCollection):
        del items
        sampled_ids = tf.random.uniform(
            (self.n_per_positive * batch_size,), maxval=batch_size, dtype=tf.int32, seed=self.seed
        )

        return sampled_ids


def _list_to_tensor(input_list: List[tf.Tensor]) -> tf.Tensor:
    output: tf.Tensor

    if len(input_list) == 1:
        output = input_list[0]
    else:
        output = tf.concat(input_list, axis=0)

    return output


ItemSamplersType = Union[ItemSampler, Sequence[Union[ItemSampler, str]], str]