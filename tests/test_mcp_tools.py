import numpy as np
import pytest
from PIL import Image

pytest.importorskip("mcp")

from colorlab import mcp_server  # noqa: E402


@pytest.fixture()
def sample_image(tmp_path):
    rng = np.random.default_rng(7)
    base = rng.random((90, 120, 1)) ** 1.3
    tint = (rng.random((90, 120, 3)) - 0.5) * 0.55
    arr = np.clip(base + tint, 0, 1)
    path = tmp_path / "sample.png"
    Image.fromarray((arr * 255).round().astype(np.uint8)).save(path)
    return path


def test_tools_are_registered():
    import asyncio

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "look_schema",
        "list_looks",
        "read_look",
        "write_look",
        "analyze_image",
        "compare_images",
        "grade_image",
        "bake_lut",
        "apply_lut_file",
        "audit_lut",
        "make_comparison_sheet",
    } <= names


def test_look_schema_and_list():
    schema = mcp_server.look_schema()
    assert "tone" in schema["fields"]
    looks = mcp_server.list_looks()["looks"]
    assert any(l["name"] == "lotus_sunset_dusk" for l in looks)


def test_read_write_look_roundtrip(tmp_path):
    data = mcp_server.read_look("lotus_sunset_dusk")
    path = tmp_path / "copy.json"
    mcp_server.write_look(str(path), data)
    assert mcp_server.read_look(str(path))["tone"] == data["tone"]


def test_analyze_image(sample_image):
    rep = mcp_server.analyze_image(str(sample_image), width=100)
    assert rep["size"][0] > 0
    assert 50 in rep["luma"]
    assert rep["hues"]


def test_grade_and_bake(sample_image, tmp_path):
    out = tmp_path / "after.png"
    res = mcp_server.grade_image(
        str(sample_image), str(out), "lotus_sunset_dusk", width=80, bake=True, lut_size=9
    )
    assert out.exists()
    assert "cube" in res
    assert res["after"]["mean_rgb"] != res["before"]["mean_rgb"]

    cube = tmp_path / "x.cube"
    info = mcp_server.bake_lut(str(cube), "lotus_sunset_dusk", size=17,
                               verify_image=str(sample_image))
    assert cube.exists()
    assert info["bake_error"]["p99_9"] < 0.05


def test_apply_and_audit(sample_image, tmp_path):
    cube = tmp_path / "y.cube"
    mcp_server.bake_lut(str(cube), "lotus_sunset_dusk", size=9)
    info = mcp_server.audit_lut(str(cube))
    assert info["size"] == 9

    out = tmp_path / "applied.png"
    res = mcp_server.apply_lut_file(str(sample_image), str(cube), str(out), width=60)
    assert out.exists() and res["lut_size"] == 9


def test_comparison_sheet(sample_image, tmp_path):
    out = tmp_path / "sheet.jpg"
    res = mcp_server.make_comparison_sheet(
        str(sample_image), str(out), ref=str(sample_image),
        look_path="lotus_sunset_dusk", width=200,
    )
    assert out.exists()
    assert res["panels"] == 3
