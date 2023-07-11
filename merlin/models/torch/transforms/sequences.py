#
# Copyright (c) 2023, NVIDIA CORPORATION.
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
from typing import Dict, List, Optional, Union

import torch
import torch.nn.functional as F
from torch import nn

from merlin.models.torch.batch import Batch, Sequence
from merlin.schema import Schema, Tags


class TabularPadding(nn.Module):
    """A PyTorch module for padding tabular sequence data.

    Parameters
    ----------
    schema : Schema
        The schema of the tabular data, which defines the column names of input features.
    max_sequence_length : Optional[int], default=None
        The maximum length of the sequences after padding.
        If None, sequences will be padded to the maximum length in the current batch.

    Example usage::
        features = {
            'feature1': torch.tensor([[4, 3], [5, 2]),
            'feature2': torch.tensor([[3,8], [7,9]])
        }
        schema = Schema(["feature1", "feature2"])
        _max_sequence_length = 10
        padding_op = TabularBatchPadding(
            schema=schema, max_sequence_length=_max_sequence_length
        )
        padded_batch = padding_op(Batch(feaures))

    Notes:
        - If the schema contains continuous list features,
        ensure that they are normalized within the range of [0, 1].
        This is necessary because we will be padding them
        to a max_sequence_length using the minimum value of 0.0.
        - The current class only supports right padding.
    """

    def __init__(
        self,
        schema: Schema,
        max_sequence_length: Optional[int] = None,
    ):
        super().__init__()
        self.schema = schema
        self.max_sequence_length = max_sequence_length
        self.features: List[str] = self.schema.column_names
        self.sparse_features = self.schema.select_by_tag(Tags.SEQUENCE).column_names
        self.padding_idx = 0

    def forward(self, inputs: Union[torch.Tensor, Dict[str, torch.Tensor]], batch: Batch) -> Batch:
        _max_sequence_length = self.max_sequence_length
        if not _max_sequence_length:
            # Infer the maximum length from the current batch
            batch_max_sequence_length = 0
            for key, val in batch.features.items():
                if key.endswith("__offsets"):
                    offsets = val
                    max_row_length = int(torch.max(offsets[1:] - offsets[:-1]))
                    batch_max_sequence_length = max(max_row_length, batch_max_sequence_length)
            _max_sequence_length = batch_max_sequence_length

        # Store the non-padded lengths of list features
        seq_inputs_lengths = self._get_sequence_lengths(batch.features)
        seq_shapes: List[torch.Tensor] = list(seq_inputs_lengths.values())
        if not torch.all(torch.stack([torch.all(x == seq_shapes[0]) for x in seq_shapes])):
            raise ValueError(
                "The sequential inputs must have the same length for each row in the batch, "
                f"but they are different: {seq_shapes}"
            )
        # Pad the features of the batch
        batch_padded = {}
        for key, value in batch.features.items():
            if key.endswith("__offsets"):
                col_name = key[: -len("__offsets")]
                if col_name in self.features:
                    padded_values = self._pad_ragged_tensor(
                        batch.features[f"{col_name}__values"], value, _max_sequence_length
                    )
                    batch_padded[col_name] = padded_values
            elif key.endswith("__values"):
                continue
            else:
                col_name = key
                if col_name in self.features and seq_inputs_lengths.get(col_name) is not None:
                    # pad dense list features
                    batch_padded[col_name] = self._pad_dense_tensor(value, _max_sequence_length)

        # Pad targets of the batch
        targets_padded = None
        if batch.targets is not None:
            targets_padded = {}
            for key, value in batch.targets.items():
                if key.endswith("__offsets"):
                    col_name = key[: -len("__offsets")]
                    padded_values = self._pad_ragged_tensor(
                        batch.targets[f"{col_name}__values"], value, _max_sequence_length
                    )
                    targets_padded[col_name] = padded_values
                elif key.endswith("__values"):
                    continue
                else:
                    targets_padded[key] = value

        return Batch(
            features=batch_padded, targets=targets_padded, sequences=Sequence(seq_inputs_lengths)
        )

    def _get_sequence_lengths(self, sequences: Dict[str, torch.Tensor]):
        """Compute the effective length of each sequence in a dictionary of sequences."""
        seq_inputs_lengths = {}
        for key, val in sequences.items():
            if key.endswith("__offsets"):
                seq_inputs_lengths[key[: -len("__offsets")]] = val[1:] - val[:-1]
            elif key in self.sparse_features:
                seq_inputs_lengths[key] = (val != self.padding_idx).sum(-1)
        return seq_inputs_lengths

    def _squeeze(self, tensor: torch.Tensor):
        """Squeeze a tensor of shape (N,1) to shape (N)."""
        if len(tensor.shape) == 2:
            return tensor.squeeze(1)
        return tensor

    def _get_indices(self, offsets: torch.Tensor, diff_offsets: torch.Tensor):
        """Compute indices for a sparse tensor from offsets and their differences."""
        row_ids = torch.arange(len(offsets) - 1, device=offsets.device)
        row_ids_repeated = torch.repeat_interleave(row_ids, diff_offsets)
        row_offset_repeated = torch.repeat_interleave(offsets[:-1], diff_offsets)
        col_ids = (
            torch.arange(len(row_offset_repeated), device=offsets.device) - row_offset_repeated
        )
        indices = torch.cat([row_ids_repeated.unsqueeze(-1), col_ids.unsqueeze(-1)], dim=1)
        return indices

    def _pad_ragged_tensor(self, values: torch.Tensor, offsets: torch.Tensor, padding_length: int):
        """Pad a ragged features represented by "values" and "offsets" to a dense tensor
        of length `padding_length`.
        """
        values = self._squeeze(values)
        offsets = self._squeeze(offsets)
        num_rows = len(offsets) - 1
        diff_offsets = offsets[1:] - offsets[:-1]
        max_length = int(diff_offsets.max())
        indices = self._get_indices(offsets, diff_offsets)
        sparse_tensor = torch.sparse_coo_tensor(
            indices.T, values, torch.Size([num_rows, max_length]), device=values.device
        )

        return self._pad_dense_tensor(sparse_tensor.to_dense(), padding_length)

    def _pad_dense_tensor(self, tensor: torch.Tensor, length: int) -> torch.Tensor:
        """Pad a dense tensor along its second dimension to a specified length."""
        if len(tensor.shape) == 2:
            pad_diff = length - tensor.shape[1]
            return F.pad(input=tensor, pad=(0, pad_diff, 0, 0))
        return tensor