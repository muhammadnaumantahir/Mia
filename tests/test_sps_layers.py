from mia.sps.layers import SPS_LAYERS, get_layer


def test_sps_has_exactly_ten_canonical_layers():
    assert len(SPS_LAYERS) == 10
    assert [layer.number for layer in SPS_LAYERS] == list(range(1, 11))


def test_layer_lookup_preserves_canonical_identity():
    assert get_layer("SPS-01").name == "Software DNA Core"
    assert get_layer("SPS-10").name == "Execution Core"
