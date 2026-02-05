# OpenClawBatchVariants

> Generate multiple deterministic variants with seed/parameter sweeps.

## Inputs

| Name | Type | Description |
|------|------|-------------|
| `positive` | STRING | Base positive prompt |
| `negative` | STRING | Base negative prompt |
| `count` | INT | Number of variants (1-100) |
| `seed_base` | INT | Starting seed value |
| `seed_policy` | COMBO | `fixed`, `increment`, `randomized` |
| `variant_policy` | COMBO | `none`, `cfg_sweep`, `steps_sweep`, `size_sweep` |
| `params_json` | STRING | (Optional) Base parameters JSON |
| `sweep_start` | FLOAT | (Optional) Sweep start value |
| `sweep_end` | FLOAT | (Optional) Sweep end value |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| `positive_list` | LIST[STRING] | List of positive prompts |
| `negative_list` | LIST[STRING] | List of negative prompts |
| `params_json_list` | LIST[STRING] | List of parameter JSONs with seed + sweep values |

## Seed Policies

- **fixed**: All variants use `seed_base`
- **increment**: Seeds are `seed_base`, `seed_base+1`, `seed_base+2`, ...
- **randomized**: Deterministic pseudo-random seeds based on `seed_base + i`

## Variant Policies

- **none**: No parameter sweep
- **cfg_sweep**: Interpolate CFG from `sweep_start` to `sweep_end`
- **steps_sweep**: Interpolate steps from `sweep_start` to `sweep_end`
- **size_sweep**: Interpolate width/height from `sweep_start` to `sweep_end`

## Example Usage

```
┌─────────────────┐     ┌───────────────────┐     ┌─────────────┐
│ PromptPlanner   │────▶│ OpenClawBatch     │────▶│ Batch       │
│                 │     │ Variants          │     │ Processor   │
└─────────────────┘     └───────────────────┘     └─────────────┘
```

## Safety Notes

- Maximum 100 variants to prevent queue flooding
- All parameters validated via `GenerationParams.from_dict()`

## Troubleshooting

- Check that `params_json` is valid JSON
- Monitor `/openclaw/health` for queue status (legacy `/moltbot/health` still works)
