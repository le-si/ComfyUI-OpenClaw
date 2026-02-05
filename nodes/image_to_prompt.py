import base64
import io
import json
import logging
from typing import Any, Dict, List, Tuple

try:
    from PIL import Image  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    Image = None  # type: ignore

try:
    import numpy as np  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    np = None  # type: ignore

try:
    from ..services.llm_client import LLMClient
    from ..services.llm_output import (
        extract_json_object,
        sanitize_list_to_string,
        sanitize_string,
    )
except ImportError:
    from services.llm_client import LLMClient
    from services.llm_output import (
        extract_json_object,
        sanitize_list_to_string,
        sanitize_string,
    )

try:
    from ..services.metrics import metrics
except ImportError:
    from services.metrics import metrics

logger = logging.getLogger("ComfyUI-OpenClaw.nodes.ImageToPrompt")


class MoltbotImageToPrompt:
    """
    Experimental node that uses Vision LLM to generate prompt starters from an image.
    """

    def __init__(self):
        self.llm_client = LLMClient()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "goal": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "Describe this image for regeneration",
                    },
                ),
                "detail_level": (["low", "medium", "high"], {"default": "medium"}),
                "max_image_side": ("INT", {"default": 1024, "min": 256, "max": 1536}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("caption", "tags", "prompt_suggestion")
    FUNCTION = "generate_prompt"
    CATEGORY = "moltbot"

    def _tensor_to_base64_png(self, tensor_image: Any, max_side: int) -> str:
        """
        Convert ComfyUI tensor (Batch, H, W, C) to base64 PNG.
        Uses the first image in batch.
        """
        if Image is None:
            raise RuntimeError(
                "Pillow (PIL) is required for ImageToPrompt. Please install pillow."
            )
        if np is None:
            raise RuntimeError(
                "numpy is required for ImageToPrompt. Please install numpy."
            )
        # Tensor is typically [Batch, H, W, 3] float32 0..1
        # Take first image
        if len(tensor_image.shape) == 4:
            img_np = tensor_image[0]
        else:
            # Handle case where it might be single image [H, W, 3]
            img_np = tensor_image

        # Check if tensor (convert to numpy if it is a torch tensor)
        if hasattr(img_np, "cpu"):
            img_np = img_np.cpu().numpy()

        # Convert to uint8 0..255
        img_np = np.clip(img_np * 255.0, 0, 255).astype(np.uint8)

        # To PIL
        pil_img = Image.fromarray(img_np)

        # Resize if needed
        width, height = pil_img.size
        max_dim = max(width, height)
        if max_dim > max_side:
            scale = max_side / max_dim
            new_w = int(width * scale)
            new_h = int(height * scale)
            pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Bytes Metadata stripping (default save doesn't add much, but good practice)
        buffered = io.BytesIO()
        pil_img.save(buffered, format="PNG", optimize=True)
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return img_str

    def generate_prompt(
        self, image: Any, goal: str, detail_level: str, max_image_side: int
    ) -> Tuple[str, str, str]:
        metrics.increment("vision_calls")

        # 1. Preprocess Image
        try:
            image_b64 = self._tensor_to_base64_png(image, max_image_side)
        except Exception as e:
            metrics.increment("errors")
            logger.error(f"Failed to preprocess image: {e}")
            raise ValueError(f"Image preprocessing failed: {e}")

        # 2. Construct System Prompt
        system_prompt = f"""
You are an expert AI art prompter. analyze the image and the user's goal.
Detail Level: {detail_level}

Output strictly valid JSON:
{{
  "caption": "Concise visual description",
  "tags": ["tag1", "tag2", "tag3"],
  "prompt_suggestion": "The actual prompt to generate this image"
}}

Do not use markdown blocks.
"""

        # 3. Construct User Message
        user_message = f"Goal: {goal}"

        try:
            # 4. Call Vision LLM
            logger.info("Sending vision request to LLM...")

            # Using updated client signature
            response = self.llm_client.complete(
                system=system_prompt, user_message=user_message, image_base64=image_b64
            )

            content = response.get("text", "")

            # Extract JSON using shared sanitizer (S3 defense)
            data = extract_json_object(content)

            if data is None:
                logger.warning("Failed to extract JSON from LLM response")
                metrics.increment("errors")
                return ("", "", "")

            # Extract with sanitization (only expected keys, treated as plain text)
            caption = sanitize_string(data.get("caption"), default="")
            tags_str = sanitize_list_to_string(data.get("tags"))
            prompt_suggestion = sanitize_string(
                data.get("prompt_suggestion"), default=""
            )

            return (caption, tags_str, prompt_suggestion)

        except Exception as e:
            metrics.increment("errors")
            logger.error(f"Failed to generate prompt from image: {e}")
            raise e
