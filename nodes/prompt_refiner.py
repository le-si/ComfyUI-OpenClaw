import base64
import io
import json
import logging
from typing import Any, Dict, List, Tuple

try:
    import numpy as np  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    np = None  # type: ignore

try:
    from PIL import Image  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    Image = None  # type: ignore

try:
    from ..services.refiner import RefinerService
except ImportError:
    from services.refiner import RefinerService

try:
    from ..services.metrics import metrics
except ImportError:
    from services.metrics import metrics

# Allowed keys (kept for documentation/reference, but service handles logic)
ALLOWED_PATCH_KEYS = {
    "steps",
    "cfg",
    "width",
    "height",
    "sampler_name",
    "scheduler",
    "seed",
}

logger = logging.getLogger("ComfyUI-OpenClaw.nodes.PromptRefiner")


class MoltbotPromptRefiner:
    """
    Critiques and refines prompts/params based on a generated image and identified issues.
    DELEGATES to services.refiner.RefinerService (F21 Refactor).
    """

    def __init__(self):
        self.service = RefinerService()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "orig_positive": ("STRING", {"multiline": True}),
                "orig_negative": ("STRING", {"multiline": True}),
                "issue": (
                    [
                        "hands_bad",
                        "face_bad",
                        "anatomy_off",
                        "lighting_off",
                        "composition_off",
                        "style_drift",
                        "text_artifacts",
                        "low_detail",
                        "other",
                    ],
                    {"default": "other"},
                ),
            },
            "optional": {
                "params_json": ("STRING", {"multiline": True, "default": "{}"}),
                "goal": ("STRING", {"multiline": True, "default": "Fix the issues"}),
                "max_image_side": ("INT", {"default": 1024, "min": 256, "max": 1536}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "refined_positive",
        "refined_negative",
        "param_patch_json",
        "rationale",
    )
    FUNCTION = "refine_prompt"
    CATEGORY = "moltbot"

    def _tensor_to_base64_png(self, tensor_image: Any, max_side: int) -> str:
        """
        Convert ComfyUI tensor (Batch, H, W, C) to base64 PNG.
        (Duplicated from ImageToPrompt for MVP robustness/isolation).
        """
        if Image is None:
            raise RuntimeError(
                "Pillow (PIL) is required for PromptRefiner. Please install pillow."
            )
        if np is None:
            raise RuntimeError(
                "numpy is required for PromptRefiner. Please install numpy."
            )
        if len(tensor_image.shape) == 4:
            img_np = tensor_image[0]
        else:
            img_np = tensor_image

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
            pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        buffered = io.BytesIO()
        pil_img.save(buffered, format="PNG", optimize=True)
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return img_str

    def refine_prompt(
        self,
        image: Any,
        orig_positive: str,
        orig_negative: str,
        issue: str,
        params_json: str = "{}",
        goal: str = "Fix the issues",
        max_image_side: int = 1024,
    ) -> Tuple[str, str, str, str]:

        # 1. Preprocess Image (Node responsibility)
        try:
            image_b64 = self._tensor_to_base64_png(image, max_image_side)
        except Exception as e:
            metrics.increment(
                "errors"
            )  # Keep metrics here for image preprocessing errors
            logger.error(f"Failed to preprocess image: {e}")
            raise ValueError(f"Image preprocessing failed: {e}")

        try:
            # Delegate to Service
            refined_pos, refined_neg, patch_dict, rationale = (
                self.service.refine_prompt(
                    image_b64=image_b64,
                    orig_positive=orig_positive,
                    orig_negative=orig_negative,
                    issue=issue,
                    params_json=params_json,
                    goal=goal,
                )
            )

            return (
                refined_pos,
                refined_neg,
                json.dumps(patch_dict, indent=2),
                rationale,
            )

        except Exception as e:
            # Fallback handled by service? Service raises. Node catches to stay robust?
            # Original code returned fallback on catch. Service raises exception.
            # We should wrap in try/except here to match original behavior if desired,
            # OR let Service handle fallback return.
            # Service implementation above returns fallback on parsing error, but raises on other exceptions.
            # Let's catch generic exception here for safety.
            metrics.increment("errors")  # Add metrics for service errors
            logger.error(f"Refiner Service failed: {e}")
            return (orig_positive, orig_negative, "{}", f"Error: {str(e)}")
