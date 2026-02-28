import base64
import io
from typing import Any

# CRITICAL: keep optional imports at module-load time so test/loader paths
# without Pillow/numpy can still import modules and fail only on image use.
try:
    from PIL import Image  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    Image = None  # type: ignore

try:
    import numpy as np  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    np = None  # type: ignore


def tensor_to_base64_png(tensor_image: Any, max_side: int, context: str) -> str:
    """
    Convert ComfyUI IMAGE tensor ([B,H,W,C] or [H,W,C]) into base64 PNG.
    """
    if Image is None:
        raise RuntimeError(
            f"Pillow (PIL) is required for {context}. Please install pillow."
        )
    if np is None:
        raise RuntimeError(f"numpy is required for {context}. Please install numpy.")

    img_np = tensor_image[0] if len(tensor_image.shape) == 4 else tensor_image

    if hasattr(img_np, "cpu"):
        img_np = img_np.cpu().numpy()

    img_np = np.clip(img_np * 255.0, 0, 255).astype(np.uint8)
    pil_img = Image.fromarray(img_np)

    width, height = pil_img.size
    max_dim = max(width, height)
    if max_dim > max_side:
        scale = max_side / max_dim
        new_w = int(width * scale)
        new_h = int(height * scale)
        resampling = getattr(Image, "Resampling", Image)
        pil_img = pil_img.resize((new_w, new_h), resampling.LANCZOS)

    buffered = io.BytesIO()
    pil_img.save(buffered, format="PNG", optimize=True)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")
