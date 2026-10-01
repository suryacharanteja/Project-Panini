"""Screenshot compression for the Jev browser loop's event stream."""

import base64
import io
import logging

try:
    from PIL import Image
except ImportError:
    Image = None


logger = logging.getLogger(__name__)


# Raw PNGs of image-heavy pages run to several MB, which stalls the event
# stream. Downscaled JPEG keeps each frame small enough to feel live.
SCREENSHOT_MAX_WIDTH = 1000
SCREENSHOT_QUALITY = 72


def compress_screenshot(raw: bytes) -> str:
    """PNG bytes -> compact base64 JPEG. Falls back to raw base64 on failure."""
    if Image is None:
        return base64.b64encode(raw).decode("ascii")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        if image.width > SCREENSHOT_MAX_WIDTH:
            ratio = SCREENSHOT_MAX_WIDTH / image.width
            image = image.resize(
                (SCREENSHOT_MAX_WIDTH, max(1, int(image.height * ratio))),
                Image.LANCZOS,
            )
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=SCREENSHOT_QUALITY, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as e:
        logger.warning("Screenshot compression failed (%s); sending raw", e)
        return base64.b64encode(raw).decode("ascii")
