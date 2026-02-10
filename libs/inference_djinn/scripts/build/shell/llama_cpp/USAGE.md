# llama-cpp-python Build Script Usage

**Updated**: 2026-01-02  
**Status**: Production-ready with smart defaults

---

## Quick Start

### Default Build (Recommended)

```bash
# Uses latest tested commit with Mixtral support (commit: 54132f1b)
# Optimized for current hardware
./libs/inference_djinn/scripts/build/shell/llama_cpp/build_llama_cpp.sh
```

This will:
- ✅ Use commit `54132f1b1` (tested with RTX 5090 + CUDA 13.0)
- ✅ Include Mixtral/MoE support
- ✅ Auto-detect and optimize for your GPU
- ✅ Build with --cpu-avx2 if forced (via --force flag)

### Use Old Pinned Stable

```bash
# Use older stable commit (no Mixtral support)
./libs/inference_djinn/scripts/build/shell/llama_cpp/build_llama_cpp.sh --pinned-llama-cpp
```

This uses commit `4227c9be` (August 2025, pre-Mixtral).

### Use Latest Master (Risky)

```bash
# Use bleeding-edge llama.cpp master
# May fail with RTX 5090 + CUDA 13.0 due to Blackwell mxfp4 features
./libs/inference_djinn/scripts/build/shell/llama_cpp/build_llama_cpp.sh --latest-llama-cpp
```

---

## Common Usage Patterns

### Portable Docker Build

```bash
# AVX2 + multi-GPU support (recommended for containers)
./build_llama_cpp.sh --cpu-avx2 --gpu-generic
```

### Force Build on AVX-512 Machine with AVX2

```bash
# Build AVX2 version on AVX-512 machine (for portability)
./build_llama_cpp.sh --cpu-avx2 --force
```

### Specific GPU Architecture

```bash
# RTX 5090 (sm_120)
./build_llama_cpp.sh --gpu-arch=120

# RTX 4090 (sm_89)
./build_llama_cpp.sh --gpu-arch=89

# RTX 3090 (sm_86)
./build_llama_cpp.sh --gpu-arch=86
```

### Maximum Portability

```bash
# Works on any x86-64 machine
./build_llama_cpp.sh --generic
```

---

## Advanced Features

### Build with Fallback

```bash
# Try primary commit, fall back to older commits if build fails
./build_llama_cpp.sh --with-fallback
```

### Build and Validate

```bash
# Build and run import + functionality tests
./build_llama_cpp.sh --validate

# Build and validate with specific model
./build_llama_cpp.sh --validate-model=/path/to/model.gguf
```

### Specific Commit

```bash
# Use exact commit (e.g., for testing)
./build_llama_cpp.sh --llama-cpp-commit=54132f1b1fa16b419d589ac03d3266178259eb25
```

### Force Rebuild

```bash
# Rebuild even if cached wheel exists
./build_llama_cpp.sh --force-rebuild
```

### Custom Cache Directory

```bash
# Use custom cache location
./build_llama_cpp.sh --cache-dir=/custom/cache/path
```

---

## Compatibility Profiles

Pre-configured profiles for common scenarios:

### stable-mixtral (Default)

```bash
./build_llama_cpp.sh --compatibility-profile=stable-mixtral
```

- Uses: commit `54132f1b1` (Dec 24, 2025)
- Features: Mixtral, MoE, RTX 5090 compatible
- Fallback: `4227c9be` (old stable)

### stable

```bash
./build_llama_cpp.sh --compatibility-profile=stable
```

- Uses: commit `4227c9be` (Aug 14, 2025)
- Features: Basic, no Mixtral
- Most conservative option

### known-good

```bash
./build_llama_cpp.sh --compatibility-profile=known-good
```

- Uses: commit `54132f1b1`
- Same as stable-mixtral but no fallback

### bleeding-edge (Not Recommended)

```bash
./build_llama_cpp.sh --compatibility-profile=bleeding-edge
```

- Uses: latest master
- May fail with RTX 5090 + CUDA 13.0

---

## Feature-Based Selection

### Mixtral Support

```bash
# Auto-selects commit with Mixtral support
./build_llama_cpp.sh --feature=mixtral
```

This is equivalent to `--compatibility-profile=stable-mixtral`.

---

## Environment Detection

The script automatically detects:

- **GPU**: Uses `nvidia-smi` to detect architecture (sm_120, sm_89, etc.)
- **CPU**: Detects AVX-512, AVX2 capabilities
- **RAM**: Checks available memory for parallel jobs
- **Disk**: Verifies sufficient space for build

---

## Build Behavior

### Default Behavior (No Arguments)

1. Detects your hardware
2. Uses `stable-mixtral` profile (commit `54132f1b1`)
3. Optimizes for detected GPU architecture
4. Uses conservative parallel jobs (half of threads)
5. Caches wheel for future use

### Commit Selection Priority

1. `--llama-cpp-commit` (explicit) → Use exact commit
2. `--latest-llama-cpp` → Use master
3. `--pinned-llama-cpp` → Use submodule pinned commit
4. `--feature` → Auto-select for feature
5. `--compatibility-profile` → Use profile commit
6. **DEFAULT** → Use `stable-mixtral` profile

---

## Known Issues

### RTX 5090 + CUDA 13.0

**Issue**: Commits after Dec 24, 2025 14:02 UTC+1 break compilation  
**Cause**: Blackwell mxfp4 features require CUDA 13.1+ or sm_120a  
**Solution**: Use default (commit `54132f1b1`) or `--pinned-llama-cpp`

**Affected commits**:
- `c8a2417d7` and newer (Dec 24 22:28 onwards)
- Latest master (`bcfc8c3ce`, Jan 2, 2026)

### AVX-512 Build on AVX-512 Machine

**Issue**: Script warns when using `--cpu-avx2` on AVX-512 capable machine  
**Solution**: Add `--force` flag if intentional (e.g., for portability)

```bash
./build_llama_cpp.sh --cpu-avx2 --force
```

---

## Troubleshooting

### Build Fails

1. **Try fallback**:
   ```bash
   ./build_llama_cpp.sh --with-fallback
   ```

2. **Use older stable**:
   ```bash
   ./build_llama_cpp.sh --pinned-llama-cpp
   ```

3. **Check logs**: Build logs are in `/tmp/llama-cpp-python-build/`

### Wrong Architecture Detected

```bash
# Manually specify GPU architecture
./build_llama_cpp.sh --gpu-arch=120
```

### Out of Memory During Build

```bash
# Use fewer parallel jobs
./build_llama_cpp.sh --jobs=4
```

### Cached Wheel Issues

```bash
# Force rebuild
./build_llama_cpp.sh --force-rebuild
```

---

## Migration from Old Script

### Old Command → New Command

| Old | New (Equivalent) |
|-----|------------------|
| `./build_llama_cpp.sh` | `./build_llama_cpp.sh --pinned-llama-cpp` |
| N/A | `./build_llama_cpp.sh` (now uses Mixtral-compatible commit) |

### Breaking Changes

**None** - The script maintains backward compatibility:
- `--pinned-llama-cpp` still works (uses commit `4227c9be`)
- All old flags are supported
- Default behavior changed to use better commit

---

## Technical Details

### Commit Information

| Profile | Commit | Date | Features | Notes |
|---------|--------|------|----------|-------|
| stable-mixtral | `54132f1b1` | 2025-12-24 | Mixtral, MoE | ✅ RTX 5090 tested |
| stable | `4227c9be` | 2025-08-14 | Basic | No Mixtral |

### Build Artifacts

- **Wheels**: `~/.cache/llama-cpp-builds/` (default)
- **Sources**: `/tmp/llama-cpp-python-build/` (cleaned after build)
- **Logs**: `/tmp/llama-cpp-python-build/*.log`

---

## Examples

### Production Deployment

```bash
# Portable, cached, validated build
./build_llama_cpp.sh --cpu-avx2 --gpu-generic --validate --use-cache
```

### Development/Testing

```bash
# Latest commit with fallback, keep sources for debugging
./build_llama_cpp.sh --latest-llama-cpp --with-fallback --keep-sources --verbose
```

### Docker Build

```bash
# Maximum portability, install to target directory
./build_llama_cpp.sh --portable --target=/build/packages --no-deps
```

### CI/CD

```bash
# Specific commit, force rebuild, validate
./build_llama_cpp.sh \
  --llama-cpp-commit=54132f1b1 \
  --force-rebuild \
  --validate \
  --cpu-avx2 \
  --gpu-generic
```

---

## Performance Notes

### CPU Optimization Impact

| Flag | Performance vs Generic | Compatibility |
|------|----------------------|---------------|
| `--cpu-native` | 3-6x faster | Current CPU only |
| `--cpu-avx512` | 4-6x faster | Intel 2019+/AMD Zen4+ |
| `--cpu-avx2` | 2-3x faster | Intel 2013+/AMD 2015+ |
| `--cpu-generic` | Baseline | Any x86-64 |

### GPU Optimization Impact

| Flag | Binary Size | Compatibility |
|------|------------|---------------|
| `--gpu-native` | ~50MB | Single GPU arch |
| `--gpu-arch=120` | ~50MB | RTX 5090 only |
| `--gpu-generic` | ~250MB | Multi-GPU |

---

## Support

**Issues**: https://github.com/ggerganov/llama.cpp/issues  
**Compatibility Matrix**: `libs/inference_djinn/scripts/build/python_builders/llama_cpp/compatibility.py`  
**Build Logs**: `/tmp/llama-cpp-python-build/`
