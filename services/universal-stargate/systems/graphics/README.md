# Graphics System

Image generation system for Universal LLM Gateway using Flux.2 models.

## Features

- OpenAI-compatible `/v1/images/generations` endpoint
- Model loading coordination via `ResourceAwareModelManager`
- HTTP forwarding with long timeout (30 min)
- OpenAI-compatible parameter mapping (`quality`/`style` → Flux.2 params)
- Caption upsampling for improved prompt adherence

## API

### POST /v1/images/generations

Generate images using Flux.2 models.

**Request**:
```json
{
  "model": "flux.2-dev",
  "prompt": "A serene mountain landscape at sunset",
  "size": "1024x1024",
  "quality": "hd",
  "style": "vivid",
  "caption_upsample_temperature": 0.15
}
```

**Response**:
```json
{
  "created": 1706123456,
  "data": [
    {
      "url": "data:image/png;base64,..."
    }
  ]
}
```

## Parameter Mapping

| OpenAI Param | Flux.2 Param | Mapping |
|--------------|--------------|---------|
| `quality: "standard"` | `num_inference_steps` | 20 |
| `quality: "hd"` | `num_inference_steps` | 50 |
| `style: "vivid"` | `guidance_scale` | 4.0 |
| `style: "natural"` | `guidance_scale` | 2.5 |

## FLUX.2-Specific Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `caption_upsample_temperature` | None | Caption upsampling strength (0.15 recommended) |
| `num_inference_steps` | 20 | Denoising steps (FLUX.2 converges faster) |
| `guidance_scale` | 4.0 | Prompt adherence (lower works well with FLUX.2) |

## Model Loading

Model loading is handled automatically by `ResourceAwareModelManager`:

1. Request received for model (e.g., `flux.2-dev`)
2. Check if model is already loaded on any gateway
3. If not, find gateway with sufficient resources
4. Load model and forward request

## Resource Requirements

| Model | VRAM | Notes |
|-------|------|-------|
| flux.2-dev | ~28GB | Full quality, 32B params |
| flux.2-dev (offload) | ~16GB | With CPU offload enabled
