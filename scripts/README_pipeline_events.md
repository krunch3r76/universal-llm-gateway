# Pipeline Event Viewer

Quick diagnostic tool to extract and display pipeline execution events in human-readable format.

## Usage

### View from event log
```bash
# Last 30 pipeline events
python scripts/view_pipeline_events.py -n 30

# Filter by execution ID
python scripts/view_pipeline_events.py -e a5bfa61d-9bdf-400e-8938-c75e15f04080

# Filter by step name
python scripts/view_pipeline_events.py -s answer_all

# Filter by pipeline ID
python scripts/view_pipeline_events.py -p consensus-basic-v3
```

### View from structured logs (if events not persisted)
```bash
# Extract pipeline events from structured logs
grep 'pipeline\.map\.' /tmp/logs/universal-stargate/universal_stargate.log | \
  jq '{signal: .logger | sub("systems.pipeline.core.execution.map_reduce.executor"; "pipeline.map"), timestamp: .["@timestamp"], data: .}' | \
  python scripts/view_pipeline_events.py /dev/stdin
```

## Output Format

```
Pipeline Events (15 events)

23:36:34.206 MAP_START answer_all (iterations=3, timeout=400.0s, threshold=None)
23:36:34.206   → answer_all #0 model=qwen2-5-7b
23:37:33.905   ✓ answer_all #0 (59.7s)
23:39:11.398 MAP_END answer_all (3/3 ok) 157.2s

23:39:11.399   → decompose_phi #0 model=qwen2-5-7b
23:41:11.402   ⏱ decompose_phi #0 (2m0.0s)
             Exceeded total timeout of 120.0s
```

## Event Types

- `MAP_START` / `MAP_END` - Map step boundaries
- `→` - Iteration started
- `✓` - Iteration completed successfully  
- `✗` - Iteration failed (error)
- `⏱` - Iteration timed out
- `⚠ TIMEOUT` - Timeout warning (75%/90% threshold)
- `💾` / `📂` - Checkpoint saved/loaded

## Known Issue

Currently, pipeline events are not being persisted to `/tmp/stargate-events/current.jsonl` by default. They're emitted via the event bus but filtered out by the persistence layer. This needs to be fixed in the stargate configuration.

For now, diagnose pipeline issues via structured logs:
```bash
grep "Map step\|completed\|failed\|timeout" /tmp/logs/universal-stargate/universal_stargate.log | tail -50
```
