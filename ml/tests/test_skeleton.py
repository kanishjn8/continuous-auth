from ml.features import FEATURE_SCHEMA_VERSION


def test_shared_feature_package_declares_protocol_compatible_version() -> None:
    assert FEATURE_SCHEMA_VERSION == "1.0.0"
