import os

import pytest

import merlin.io
from merlin.models.data.ecommerce import aliccp
from merlin.models.data.synthetic import generate_data

# This is the path to the raw ali-ccp dataset
MAYBE_ALICCP_DATA = os.environ.get("DATA_PATH_ALICCP", None)


def test_synthetic_aliccp_data():
    dataset = generate_data("aliccp", 100)

    assert isinstance(dataset, merlin.io.Dataset)
    assert dataset.num_rows == 100
    assert len(dataset.schema) == 20


def test_synthetic_aliccp_raw_data():
    dataset = generate_data("aliccp-raw", 100)

    assert isinstance(dataset, merlin.io.Dataset)
    assert dataset.num_rows == 100
    assert len(dataset.schema) == 10


@pytest.mark.skipif(
    MAYBE_ALICCP_DATA is None,
    reason="ALI-CCP data is not available, pass it through env variable $DATA_PATH_ALICCP",
)
def test_get_alliccp():
    data_path = MAYBE_ALICCP_DATA

    train, valid = aliccp.get_aliccp(data_path, overwrite=True)

    assert isinstance(train, merlin.io.Dataset)
    assert isinstance(valid, merlin.io.Dataset)


@pytest.mark.skipif(
    MAYBE_ALICCP_DATA is None,
    reason="ALI-CCP data is not available, pass it through env variable $DATA_PATH_ALICCP",
)
def test_prepare_alliccp(tmp_path):
    data_path = MAYBE_ALICCP_DATA

    aliccp.prepare_alliccp(data_path, file_size=50, max_num_rows=100, output_dir=tmp_path)
    output_files = list(tmp_path.glob("*/*"))

    assert len(output_files) == 2
    assert all(f.name.endswith(".parquet") for f in output_files)


@pytest.mark.skipif(
    MAYBE_ALICCP_DATA is None,
    reason="ALI-CCP data is not available, pass it through env variable $DATA_PATH_ALICCP",
)
def test_transform_alliccp(tmp_path):
    data_path = MAYBE_ALICCP_DATA

    aliccp.transform_aliccp(data_path, tmp_path)
    output_files = list(tmp_path.glob("*/*"))

    assert len(output_files) == 10
