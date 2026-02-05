# OpenClawImageToPrompt

> Generate prompts from images using vision LLMs.

## Inputs

| Name | Type | Description |
|------|------|-------------|
| `image` | IMAGE | Input image to analyze |
| `goal` | STRING | Analysis goal (e.g., "Describe for regeneration") |
| `detail_level` | COMBO | `low`, `medium`, `high` |
| `max_image_side` | INT | Max dimension for resizing (256-1536) |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| `caption` | STRING | Descriptive caption of the image |
| `tags` | STRING | Comma-separated style/content tags |
| `prompt_suggestion` | STRING | Suggested prompt to recreate the image |

## Example Usage

```
┌─────────────────┐     ┌───────────────────┐     ┌─────────────┐
│ Load Image      │────▶│ OpenClawImage     │────▶│ KSampler    │
│                 │     │ ToPrompt          │     │ (img2img)   │
└─────────────────┘     └───────────────────┘     └─────────────┘
```

1. Load an image you want to analyze
2. Set detail level and goal
3. Use `prompt_suggestion` as input for generation

## Safety Notes

- **S3**: LLM responses sanitized
- **S4**: Image data encoded as base64 (no file path exposure)
- Requires vision-capable model (e.g., `gpt-4o`, `claude-3-5-sonnet`)

## Troubleshooting

- Verify provider supports vision (OpenAI/Anthropic)
- Check `/openclaw/health` for API status (legacy `/moltbot/health` still works)
