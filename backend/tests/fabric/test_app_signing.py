"""V2-14 signed phone-app assets: updates activate only when signed by your PC and every hash matches."""

from backend.fabric import app_signing
from backend.fabric.devices import PcIdentity

from .conftest import MemorySecrets


def test_only_assets_signed_by_your_pc_activate(tmp_path):
    identity = PcIdentity.load(MemorySecrets())
    (tmp_path / "index.html").write_text("<h1>PANINI</h1>")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text("console.log('ok')")
    signed = app_signing.sign(identity, tmp_path, "1.0.0")
    assets = {"index.html": b"<h1>PANINI</h1>", "js/app.js": b"console.log('ok')"}
    assert app_signing.verify(signed, identity.sign_pub, assets)
    assert not app_signing.verify(
        signed, identity.sign_pub, {**assets, "js/app.js": b"steal()"}
    )  # Modified asset.
    assert not app_signing.verify(signed, identity.sign_pub, {**assets, "extra.js": b"x"})  # Injected asset.
    other = PcIdentity.load(MemorySecrets())
    assert not app_signing.verify(signed, other.sign_pub, assets)  # Signed by someone else.
