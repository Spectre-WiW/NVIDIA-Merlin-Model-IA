#
# Copyright (c) 2022, NVIDIA CORPORATION.
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

#!/bin/bash
set -e

# Call this script with:
# 1. Name of container as first parameter
#    [merlin-hugectr,  merlin-tensorflow,  merlin-pytorch]
#
# 2. Devices to use:
#    [0; 0,1; 0,1,..,n-1]

# Get last Models version
cd /models/
#git pull origin main

container=$1
devices=$2
if [ "$container" == "merlin-tensorflow" ]; then
    CUDA_VISIBLE_DEVICES="$devices" TF_MEMORY_ALLOCATION=0.8 python -m pytest -rxs tests/integration/tf/retrieval
    # TODO: When the example notebooks integration tests are fixed, change to python -m pytest -rxs tests/integration/tf/
fi
