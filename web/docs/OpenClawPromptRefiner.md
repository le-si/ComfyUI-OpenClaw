# OpenClawPromptRefiner

> AI-powered prompt refinement for specific quality issues.

## Inputs

| Name | Type | Description |
|------|------|-------------|
| `image` | IMAGE | Generated image with issues |
| `orig_positive` | STRING | Original positive prompt |
| `orig_negative` | STRING | Original negative prompt |
| `issue` | COMBO | Issue type (see below) |
| `params_json` | STRING | (Optional) Original parameters |
| `goal` | STRING | (Optional) Refinement goal |
| `max_image_side` | INT | (Optional) Max dimension for resizing |

## Issue Types

| Issue | Description |
|-------|-------------|
| `hands_bad` | Deformed or extra fingers/hands |
| `face_bad` | Distorted facial features |
| `anatomy_off` | Body proportion issues |
| `lighting_off` | Inconsistent or poor lighting |
| `composition_off` | Framing/layout problems |
| `style_drift` | Style inconsistency |
| `text_artifacts` | Garbled text in image |
| `low_detail` | Missing fine details |
| `other` | General improvements |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| `refined_positive` | STRING | Improved positive prompt |
| `refined_negative` | STRING | Improved negative prompt |
| `param_patch_json` | STRING | JSON with suggested parameter changes |
| `rationale` | STRING | Explanation of changes |

## Example Usage

```
┌─────────────────┐     ┌───────────────────┐     ┌─────────────┐
│ Generated Image │────▶│ OpenClawPrompt    │────▶│ KSampler    │
│ + orig prompts  │     │ Refiner           │     │ (retry)     │
└─────────────────┘     └───────────────────┘     └─────────────┘
```

## Safety Notes

- **S3**: LLM outputs sanitized
- Allowed patch keys: `steps`, `cfg`, `width`, `height`, `sampler_name`, `scheduler`, `seed`

## Troubleshooting

- Check `/openclaw/health` for API status (legacy `/moltbot/health` still works)
- Review `rationale` output for understanding changes
