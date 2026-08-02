---
trigger_match_terms: ["image-video-generation", "image_video_generation", "image", "video", "heic-to-video", "gen", "tooling-observability", "generation", "creation", "grok_imagine", "openai_imagine", "llm_generate"]
description: On any image generation, video creation, HEIC-to-video, grok_imagine, openai_imagine, or llm_generate request — covers dispatch overflow tools and the HEIC conversion recipe.
---

# Image and Video Generation via MCP

Trigger: generating images, animating photos, creating videos, or using xAI Grok Imagine / OpenAI image models via MCP.

## Dispatch invariant

`grok_imagine`, `openai_imagine`, and `llm_generate` live in `dispatch` overflow, not the primary MCP surface. If uncertain, discover overflow tools first.

Default: caller invokes `dispatch(tool="...")` directly. Generated output is a URL or text blob; downstream agents need MCP only if they must invoke generation themselves.

## Tools

### xAI image/video: `grok_imagine`

Handles image, image editing, and video. Video submits and polls automatically.

```python
# text→image
dispatch(tool="grok_imagine", arguments='{"model":"grok-imagine-image","prompt":"...","aspect_ratio":"16:9","n":1}')

# image→video
dispatch(tool="grok_imagine", arguments='{"model":"grok-imagine-video","prompt":"...","image_url":"https://...","aspect_ratio":"9:16","duration":8,"poll_timeout":180}')

# image edit
dispatch(tool="grok_imagine", arguments='{"model":"grok-imagine-image","prompt":"Make the background a snowy mountain","image_url":"https://..."}')
```

`image_url` must be public URL or `data:image/...;base64,...`.

### OpenAI image: `openai_imagine`

```python
dispatch(tool="openai_imagine", arguments='{"model":"gpt-image-1","prompt":"...","size":"1024x1024","quality":"high"}')
```

Other known models: `dall-e-3`, `gpt-image-1.5`, `chatgpt-image-latest`.

### Text generation: `llm_generate`

```python
dispatch(tool="llm_generate", arguments='{"messages":[{"role":"user","content":"..."}],"model":"xai/grok-4.20-0309-reasoning","max_tokens":1000}')
```

Model ID rules: `anthropic/...`, `xai/...`, `openai/...`, `openrouter/google/...`, or local no-slash ID. Google/Qwen/Meta/Mistral require `openrouter/` prefix; bare `google/gemini-*` ⇒ 404.

## HEIC phone photo → video

xAI video needs a public URL.

1. Convert/resize HEIC on host:

```bash
convert /mnt/torus/mcp-data/files/dropbox/YYYY-MM-DD/photo.heic -resize 1280x -quality 90 /tmp/photo.jpg
identify /tmp/photo.jpg
```

2. Upload:

```bash
PUBLIC_URL=$(curl -s -F "reqtype=fileupload" -F "fileToUpload=@/tmp/photo.jpg" https://catbox.moe/user/api.php)
```

Catbox worked 2026-04-25; 0x0.st was upload-disabled. Fallbacks: transfer.sh or base64 data URI (confirmed for editing; untested for video).

3. Generate video with `grok-imagine-video`; phone photos usually use `aspect_ratio:"9:16"`.

Cortex sandbox file path maps to host path: `dropbox/...` ⇒ `/mnt/torus/mcp-data/files/dropbox/...`.

## Environment

API keys (`XAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are process/shell env and consumed by Stargate cloud proxy; agents do not pass keys manually. They are not necessarily in `~/.gateway/mcp.yaml`.

## Prompt craft: image→video

`source_photo = content/style_seed`, not compositional contract. The model preserves subject appearance but may alter scale/framing unless instructed.

Use frame percentage, not distance:

```text
The elephant starts small in the frame, occupying roughly 20% of screen height, clearly in the distance. Over 5 seconds it walks directly toward camera, growing steadily larger until it fills most of the frame.
```

2026-04-25 findings:
- spatial concept small→large works with frame-percentage language;
- smooth rate control, reliable camera motion, and photorealism are weak;
- `dolly in` / `slow push` directives are unreliable;
- Veo 3 is the better target for camera control, photorealism, native audio (`todo:wire-veo-video-generation`).

| Goal | Prompt ingredient |
|---|---|
| distant start | “occupies roughly 20% of screen height at the start” |
| approach | “walks directly toward camera, growing steadily larger until it fills the frame” |
| locked camera | “locked camera, subject animates in place, no zoom” |
| preserve framing | “maintain the subject's position in frame” |

Comedy image→video: animate what exists; do not ask for new props/clothes. Lead with action verbs, use small pacing verbs, prefer reaction beats, let captions carry jokes, and keep prompts short/motion-focused.

## File size

`480p + 5s ≈ 6MB`; `480p + 8s ≈ 9–10MB`. Re-encode if needed: `ffmpeg -i input.mp4 -crf 28 -preset fast output.mp4`.
