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

from enum import Enum
from typing import Optional, Union

import tensorflow as tf
import transformers
from transformers import (
    AlbertConfig,
    BertConfig,
    GPT2Config,
    PretrainedConfig,
    RobertaConfig,
    TFPreTrainedModel,
    XLNetConfig,
)

from merlin.models.tf.core import combinators
from merlin.models.tf.core.base import Block
from merlin.models.tf.typing import TabularData
from merlin.models.tf.utils.tf_utils import (
    maybe_deserialize_keras_objects,
    maybe_serialize_keras_objects,
)

TransformerBody = Union[TFPreTrainedModel, PretrainedConfig, tf.keras.layers.Layer]


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class SelectHFOutput(tf.keras.layers.Layer):
    """Select outputs from the HF dataclass object
        `TFBaseModelOutputWithPoolingAndCrossAttentions`
    Parameters
    ----------
    output_fn: Callable
        A function to select the desirable outputs
    """

    def __init__(self, output_fn, **kwargs):
        super().__init__(**kwargs)
        self.output_fn = output_fn

    def call(self, inputs):
        return self.output_fn(inputs)


class HFOutput(Enum):
    """Enumerate different options for post-processing the TransformerBlock output"""

    last_hidden_state = SelectHFOutput(output_fn=lambda x: x.last_hidden_state)
    pooler_output = SelectHFOutput(output_fn=lambda x: x.pooler_output)
    hidden_state = SelectHFOutput(output_fn=lambda x: x.hidden_states)
    attentions = SelectHFOutput(output_fn=lambda x: x.attentions)
    last_state_and_attention = SelectHFOutput(
        output_fn=lambda x: (x.last_hidden_state, x.attentions)
    )


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class TransformerPrepare(tf.keras.layers.Layer):
    """Prepare additional inputs to the transformer layer,
    such as attention_mask or head_mask
    Parameters
    ----------
    transformer : TFPreTrainedModel
        The HuggingFace transformer model
    """

    def __init__(self, transformer: TFPreTrainedModel, **kwargs):
        super().__init__(**kwargs)
        self.transformer = transformer

    def call(self, inputs: TabularData, mask=None, features=None, **kwargs) -> TabularData:
        """Update the input embeddings with additional tensors"""
        raise NotImplementedError()


def get_tf_main_layer(hf_model):
    """
    Extract serializable custom keras layer `TF*MainLayer` from the HuggingFace model
    """
    main_layer = [v for _, v in hf_model.__dict__.items() if isinstance(v, tf.keras.layers.Layer)][
        0
    ]
    return main_layer


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class TransformerBlock(Block):
    """
    Class to support HF Transformers for session-based and sequential-based recommendation models.
    Parameters
    ----------
    transformer: TransformerBody
        The T4RecConfig, The pre-trained HF model or the custom keras layer TF*MainLayer,
        related to specific transformer architecture.
    pre: Optional[TransformerPrepare]
        A block to use before the main transformer layer, by default None
    post: Optional[Union[str, tf.keras.layers.Layer]]
        A block to use after the main transformer layer, by default 'last_hidden_state'
    """

    def __init__(
        self,
        transformer: TransformerBody,
        post: Optional[Union[str, tf.keras.layers.Layer]] = "last_hidden_state",
        pre: Optional[TransformerPrepare] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        if isinstance(transformer, PretrainedConfig):
            model_cls = transformers.TF_MODEL_MAPPING[transformer.__class__]
            self.transformer = get_tf_main_layer(model_cls(transformer))
        elif isinstance(transformer, TFPreTrainedModel):
            self.transformer = get_tf_main_layer(transformer)
        else:
            self.transformer = transformer

        # Build the post block : select output + post-process if specified
        if isinstance(post, str):
            post = HFOutput[post].value
        self.post = post

        self.pre = pre
        if pre and issubclass(pre, TransformerPrepare):
            self.pre = self.pre(self.transformer)

    def get_config(self):
        config = super().get_config()
        config = maybe_serialize_keras_objects(self, config, ["transformer", "prepare_module"])
        return config

    @classmethod
    def from_config(cls, config):
        config = maybe_deserialize_keras_objects(config, ["transformer", "prepare_module"])

        return super().from_config(config)

    def call(self, inputs: tf.Tensor, **kwargs):
        """
        Parameters
        ----------
        inputs: tf.Tensor
            The 3D tensor of the sequence of interactions embeddings.
        """
        if isinstance(inputs, tf.RaggedTensor):
            # convert to a dense tensor as HF transformers do not support ragged tensors
            inputs = inputs.to_tensor()
        inputs = {"inputs_embeds": inputs}

        return combinators.call_sequentially(list(self.to_call), inputs=inputs, **kwargs)

    @property
    def to_call(self):
        if self.pre:
            yield self.pre

        yield self.transformer

        if self.post:
            yield self.post


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class BertBlock(TransformerBlock):
    """
    Class to prepare the configuration of a `Bert` model
    """

    @classmethod
    def build_transformer_layer(
        cls,
        d_model,
        n_head,
        n_layer,
        max_seq_length,
        hidden_act="gelu",
        initializer_range=0.01,
        layer_norm_eps=0.03,
        dropout=0.3,
        pad_token=0,
        log_attention_weights=False,
        post="last_hidden_state",
        pre: TransformerPrepare = None,
        **kwargs,
    ):

        transformer = BertConfig(
            hidden_size=d_model,
            num_attention_heads=n_head,
            num_hidden_layers=n_layer,
            max_position_embeddings=max_seq_length,
            hidden_act=hidden_act,
            intermediate_size=d_model * 4,
            hidden_dropout_prob=dropout,
            attention_probs_dropout_prob=dropout,
            initializer_range=initializer_range,
            layer_norm_eps=layer_norm_eps,
            output_attentions=log_attention_weights,
            vocab_size=1,
            **kwargs,
        )

        return cls(transformer=transformer, post=post, pre=pre)


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class AlbertBlock(TransformerBlock):
    """
    Class to prepare the configuration of an `Albert` model
    """

    @classmethod
    def build_transformer_layer(
        cls,
        d_model,
        n_head,
        n_layer,
        max_seq_length,
        hidden_act="gelu",
        initializer_range=0.01,
        layer_norm_eps=0.03,
        dropout=0.3,
        pad_token=0,
        log_attention_weights=False,
        post="last_hidden_state",
        pre: TransformerPrepare = None,
        **kwargs,
    ):

        transformer = AlbertConfig(
            hidden_size=d_model,
            num_attention_heads=n_head,
            num_hidden_layers=n_layer,
            hidden_act=hidden_act,
            intermediate_size=d_model * 4,
            hidden_dropout_prob=dropout,
            attention_probs_dropout_prob=dropout,
            max_position_embeddings=max_seq_length,
            initializer_range=initializer_range,
            layer_norm_eps=layer_norm_eps,
            output_attentions=log_attention_weights,
            vocab_size=1,
            **kwargs,
        )

        return cls(transformer=transformer, post=post, pre=pre)


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class RobertaBlock(TransformerBlock):
    """
    Class to prepare the configuration of a `RoBerta` model
    """

    @classmethod
    def build_transformer_layer(
        cls,
        d_model,
        n_head,
        n_layer,
        max_seq_length,
        hidden_act="gelu",
        initializer_range=0.01,
        layer_norm_eps=0.03,
        dropout=0.3,
        pad_token=0,
        log_attention_weights=False,
        post="last_hidden_state",
        pre: TransformerPrepare = None,
        **kwargs,
    ):

        transformer = RobertaConfig(
            hidden_size=d_model,
            num_hidden_layers=n_layer,
            num_attention_heads=n_head,
            max_position_embeddings=max_seq_length,
            hidden_act=hidden_act,
            initializer_range=initializer_range,
            layer_norm_eps=layer_norm_eps,
            dropout=dropout,
            pad_token_id=pad_token,
            output_attentions=log_attention_weights,
            vocab_size=1,
            **kwargs,
        )

        return cls(transformer=transformer, post=post, pre=pre)


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class XLNetBlock(TransformerBlock):
    """
    Class to prepare the configuration of a `XLNet` model
    """

    @classmethod
    def build_transformer_layer(
        cls,
        d_model,
        n_head,
        n_layer,
        max_seq_length,
        total_seq_length=None,
        attn_type="bi",
        hidden_act="gelu",
        initializer_range=0.01,
        layer_norm_eps=0.03,
        dropout=0.3,
        pad_token=0,
        log_attention_weights=False,
        mem_len=1,
        post="last_hidden_state",
        pre: TransformerPrepare = None,
        **kwargs,
    ):

        transformer = XLNetConfig(
            d_model=d_model,
            d_inner=d_model * 4,
            n_layer=n_layer,
            n_head=n_head,
            attn_type=attn_type,
            ff_activation=hidden_act,
            initializer_range=initializer_range,
            layer_norm_eps=layer_norm_eps,
            dropout=dropout,
            pad_token_id=pad_token,
            output_attentions=log_attention_weights,
            vocab_size=1,
            mem_len=mem_len,
            **kwargs,
        )

        return cls(transformer=transformer, post=post, pre=pre)


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class GPT2Block(TransformerBlock):
    """
    Class to prepare the configuration of a `GPT2` model
    """

    @classmethod
    def build_transformer_layer(
        cls,
        d_model,
        n_head,
        n_layer,
        max_seq_length,
        hidden_act="gelu",
        initializer_range=0.01,
        layer_norm_eps=0.03,
        dropout=0.3,
        pad_token=0,
        log_attention_weights=False,
        post="last_hidden_state",
        pre: TransformerPrepare = None,
        **kwargs,
    ):

        transformer = GPT2Config(
            n_embd=d_model,
            n_inner=d_model * 4,
            n_layer=n_layer,
            n_head=n_head,
            activation_function=hidden_act,
            initializer_range=initializer_range,
            layer_norm_eps=layer_norm_eps,
            resid_pdrop=dropout,
            embd_pdrop=dropout,
            attn_pdrop=dropout,
            n_positions=max_seq_length,
            n_ctx=max_seq_length,
            output_attentions=log_attention_weights,
            vocab_size=1,
            **kwargs,
        )

        return cls(transformer=transformer, post=post, pre=pre)
