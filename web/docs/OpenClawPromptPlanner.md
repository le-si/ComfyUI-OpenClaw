# OpenClawPromptPlanner

> AI-powered prompt and parameter generation from natural language.

## Inputs

| Name | Type | Description |
|------|------|-------------|
| `profile` | COMBO | Model profile: `SDXL-v1`, `Flux-Dev` |
| `requirements` | STRING | Natural language description of desired image |
| `style_directives` | STRING | Style hints (e.g., "photorealistic, 8k, cyberpunk") |
| `seed` | INT | Random seed for reproducibility |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| `positive` | STRING | Generated positive prompt |
| `negative` | STRING | Generated negative prompt |
| `params_json` | STRING | JSON with generation parameters (width, height, steps, cfg, sampler_name, scheduler) |

## Example Usage

```
┌─────────────────┐     ┌───────────────────┐     ┌─────────────┐
│ Text Input      │────▶│ OpenClawPrompt    │────▶│ KSampler    │
│ (requirements)  │     │ Planner           │     │             │
└─────────────────┘     └───────────────────┘     └─────────────┘
```

1. Enter your requirements in natural language
2. Select target profile (SDXL-v1 or Flux-Dev)
3. Connect `positive` to positive conditioning
4. Connect `negative` to negative conditioning
5. Parse `params_json` for sampler settings

## Safety Notes

- **S3**: LLM output is sanitized and validated against allowed keys
- API key required: `OPENCLAW_LLM_API_KEY` (legacy: `MOLTBOT_LLM_API_KEY`)
- Allowed param keys: `width`, `height`, `steps`, `cfg`, `sampler_name`, `scheduler`

## Troubleshooting

- Check `/openclaw/health` for API key and provider status (legacy `/moltbot/health` still works)
- Review logs at state directory for detailed errors
