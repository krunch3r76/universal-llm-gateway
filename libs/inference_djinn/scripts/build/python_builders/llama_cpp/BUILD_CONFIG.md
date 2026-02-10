# llama-cpp-python Build Configuration Notes

## TL;DR: What Actually Matters

| Flag Category | What's Honored | Effect |
|---------------|----------------|--------|
| `-march=` in CFLAGS | ✅ Always | Compiler optimization level |
| `GGML_NATIVE` | ✅ Always | ON = use -march=native; OFF = use explicit flags |
| `CMAKE_CUDA_ARCHITECTURES` | ✅ Always | GPU code generation targets |
| `GGML_AVX512/AVX2/FMA` | ⚠️ Misleading | Does NOT exclude SIMD paths from binary |

**Key insight:** llama.cpp compiles ALL SIMD variants regardless of flags. Runtime detection picks the fastest path. The `-march` flag is the only meaningful CPU optimization lever.

## Exit Codes (for CI)

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | CPU mode mismatch (explicit mode below host capability) |

## Critical: CMake Variable Names

**llama.cpp uses `GGML_*` prefix for all cmake options, NOT `LLAMA_*`.**

Incorrect (ignored by cmake):
```cmake
-DLLAMA_AVX512=OFF
-DLLAMA_AVX2=ON
```

Correct:
```cmake
-DGGML_AVX512=OFF
-DGGML_AVX2=ON
```

## GGML_NATIVE Flag Behavior

llama.cpp's cmake has a key flag that controls how SIMD options are handled:

```cmake
option(GGML_NATIVE "ggml: optimize the build for the current system" ${GGML_NATIVE_DEFAULT})

if (GGML_NATIVE OR NOT GGML_NATIVE_DEFAULT)
    set(INS_ENB OFF)  # Native: use -march=native, skip individual SIMD flags
else()
    set(INS_ENB ON)   # Not native: individual SIMD options default to ON
endif()

option(GGML_AVX2 "ggml: enable AVX2" ${INS_ENB})  # Defaults to INS_ENB
```

### Build Modes

**Native Build (`--cpu-native`):**
- Set `GGML_NATIVE=ON`
- Skip individual SIMD flags (they're ignored anyway)
- Use `-march=native -mtune=native` in CFLAGS
- llama.cpp handles everything automatically

**Portable Build (`--cpu-avx2`, `--cpu-generic`):**
- Set `GGML_NATIVE=OFF`
- Explicitly set individual flags: `GGML_AVX512=OFF`, `GGML_AVX2=ON`, etc.
- Use specific `-march` flags (`-march=x86-64-v3` for AVX2, `-march=x86-64` for generic)

## Runtime CPU Detection

**Important:** Even with explicit SIMD flags, llama.cpp compiles ALL SIMD variants and uses runtime CPU detection to select the optimal code path.

Evidence: Binary contains AVX-512 instructions (`zmm` registers) even with `GGML_AVX512=OFF`.

This means:
- SIMD flags control compile-time *availability*, not exclusion
- Runtime detection always picks the fastest path your CPU supports
- For truly "generic" builds that only use baseline x86-64, you may need additional patches

## Build Machine Validation

Since llama.cpp uses runtime SIMD detection, **building with a lower optimization level than
your build machine supports is generally counterproductive**:
- Compiler generates code optimized for the lower `-march` target
- Runtime still uses higher SIMD paths (but code wasn't optimized for them)

**The build script enforces this by default:** if you specify `--cpu-avx2` on an AVX-512 machine,
the build will abort with an error.

### Default Solutions:
1. Use `--cpu-native` (recommended for local/baremetal builds)
2. Use `--cpu-avx512` to match your machine
3. Build on a machine that matches your target deployment (e.g., Docker on AVX2 host)

### When to Override: The `--force` Flag

**However**, there are valid use cases for building AVX2 on AVX-512 machines:

```bash
python3 build_llama_cpp.py --cpu-avx2 --gpu-native --force
```

**Valid use cases:**
1. **Workload-specific optimization**: Some hybrid GPU+CPU workloads perform better with focused AVX2 optimization
2. **Compiler focus**: Building specifically for AVX2 can produce more optimized AVX2 code paths
3. **Testing/comparison**: Compare performance between AVX2 and AVX-512 optimized builds
4. **Target deployment matching**: Build for AVX2 deployment targets on AVX-512 build machine

**Example:** In production testing, an AVX2-optimized build on Intel i9-13900K (which lacks AVX-512 anyway) 
produced 64% more AVX2 instructions and better streaming performance for hybrid GPU+CPU inference 
compared to a native AVX-512 build on AMD Ryzen 9 7900X.

**Important:** The `--force` flag shows a warning and requires explicit acknowledgment that you 
understand the trade-offs.

This validation only applies to explicit modes. `--cpu-native` always succeeds (uses auto-detection).

## GPU Architecture Targeting

GPU architecture is controlled via `CMAKE_CUDA_ARCHITECTURES`:

```cmake
# Multi-arch (portable, larger binary)
-DCMAKE_CUDA_ARCHITECTURES=80;86;87;89;90;120

# Single-arch (optimized for specific GPU)
-DCMAKE_CUDA_ARCHITECTURES=120  # RTX 5090 (Blackwell)
```

## Build Script Interface

The `build_llama_cpp.py` script accepts:

```bash
# CPU optimization (mutually exclusive)
--cpu-native      # GGML_NATIVE=ON + -march=native (default for baremetal)
--cpu-avx512      # GGML_NATIVE=OFF + GGML_AVX512=ON + -march=x86-64-v4
--cpu-avx2        # GGML_NATIVE=OFF + GGML_AVX2=ON + -march=x86-64-v3
--cpu-generic     # GGML_NATIVE=OFF + all SIMD OFF + -march=x86-64

# GPU optimization (mutually exclusive)
--gpu-native      # Auto-detect single architecture (default for baremetal)
--gpu-generic     # Multi-arch (portable)
--gpu-arch=CODE   # Specific architecture (e.g., 120 for RTX 5090)

# Validation override
--force           # Allow building lower CPU mode than machine supports
                  # Use when you specifically want AVX2 optimization on AVX-512 machine

# Docker/install options
--target=DIR      # Install to specific directory (e.g., --target=/build/packages)
--no-deps         # Don't install dependencies (preserve existing packages like PyTorch)
--keep-sources    # Don't clean up /tmp/llama-cpp-python-build after install

# llama.cpp version
--pinned-llama-cpp  # Use pinned commit (default, stable)
--latest-llama-cpp  # Use latest llama.cpp master (may have API breaks)

# Build parallelism
--jobs=N            # Use N parallel jobs
--jobs-max          # Use nproc-1 jobs
--jobs-conservative # Use nproc/2 jobs (default)
```

### CPU Optimization Details

| Flag | -march | GGML_NATIVE | What It Actually Does |
|------|--------|-------------|----------------------|
| `--cpu-native` | native | ON | Compiler optimizes for build machine; runtime auto-detects SIMD |
| `--cpu-avx512` | x86-64-v4 | OFF | Compiler assumes AVX-512 baseline; runtime auto-detects SIMD |
| `--cpu-avx2` | x86-64-v3 | OFF | Compiler assumes AVX2 baseline; runtime auto-detects SIMD |
| `--cpu-generic` | x86-64 | OFF | Compiler uses baseline x86-64; runtime auto-detects SIMD |

**Note:** The GGML_AVX512/AVX2 flags are set but **do not exclude SIMD code paths** from the binary.
llama.cpp always compiles all variants. The `-march` flag is what matters for compiler optimization.

## Source Location

Build sources are staged in `/tmp/llama-cpp-python-build/` to avoid NFS overhead:
- `/tmp/llama-cpp-python-build/llama-cpp-python/` - Source tree
- `/tmp/llama_cpp_wheel_*/` - Wheel output (temporary)

Use `--keep-sources` to preserve for inspection after build.

## Verifying Build Flags

After building, check what was actually compiled:

```bash
# Check for AVX-512 instructions (zmm registers = AVX-512)
objdump -d ~/.venvs/universal/lib/python3.12/site-packages/llama_cpp/lib/libggml-cpu.so | grep -c zmm

# Check for AVX2 instructions (ymm registers)
objdump -d ~/.venvs/universal/lib/python3.12/site-packages/llama_cpp/lib/libggml-cpu.so | grep -c ymm

# Check exported symbols for SIMD functions
nm -D ~/.venvs/universal/lib/python3.12/site-packages/llama_cpp/lib/libggml-cpu.so | grep -i avx

# Check CUDA architecture in binary
strings ~/.venvs/universal/lib/python3.12/site-packages/llama_cpp/lib/libggml-cuda.so | grep -E 'sm_[0-9]+'
```

## Common Issues

### 1. AVX-512 Code Present Despite `--cpu-generic`
**Symptom:** Binary contains `zmm` registers even with `--cpu-generic`
**Cause:** This is expected behavior - llama.cpp compiles ALL SIMD variants regardless of flags
**Reality:** The `-march` flag affects compiler optimization, not which code paths exist.
Runtime detection picks the fastest available path.

### 2. Native Build Still Multi-Arch GPU
**Symptom:** Large CUDA binary despite `--gpu-native`
**Cause:** GPU detection failed or `multi` was used
**Fix:** Explicitly pass `--gpu-arch=120` (or detected architecture)

### 3. Performance Same Across CPU Modes
**Cause:** Runtime CPU detection picks fastest available, OR GPU is the bottleneck
**Verification:** Check GPU utilization during inference - if near 100%, CPU SIMD doesn't matter

### 4. Build Aborts With CPU Mode Mismatch
**Symptom:** `❌ CPU mode mismatch: --cpu-avx2 requested, but build machine supports avx512`
**Cause:** Explicit CPU mode is lower than build machine capability
**Fix Options:**
1. Use `--cpu-native` for local builds (recommended default)
2. Build on hardware matching your target deployment
3. Use `--force` flag if you specifically want AVX2 optimization (see "When to Override" section above)

## Guidance for Future Refactors

### What NOT to Do

1. **Don't add more SIMD flags thinking they control code inclusion**
   - We tried `GGML_AVX512=OFF` expecting it to exclude AVX-512 code. It doesn't.
   - llama.cpp always compiles all SIMD variants for runtime dispatch.
   - The only way to truly exclude SIMD code would be patching llama.cpp source.

2. **Don't add legacy `LLAMA_*` flags**
   - Modern llama.cpp uses `GGML_*` prefix exclusively.
   - `LLAMA_FAST`, `LLAMA_HUGE`, `LLAMA_CUDA_*` were removed/renamed upstream.
   - We previously carried these for "compatibility" - they were no-ops.

3. **Don't bypass the CPU mode validation without good reason**
   - The `--force` flag exists for valid use cases (workload-specific optimization, testing, etc.)
   - Use it deliberately, not by default
   - Understand the trade-offs: compiler focuses on target architecture, runtime may use higher SIMD
   - In some workloads (e.g., hybrid GPU+CPU inference), focused AVX2 optimization outperforms native builds

4. **Don't assume CUDA tuning flags have measurable impact**
   - `GGML_CUDA_DMMV_X/Y`, `GGML_CUDA_MMV_Y` are matrix-vector tuning params.
   - They may or may not be honored by current llama.cpp versions.
   - Benchmark before adding more; remove if no measurable benefit.

### What Actually Works

1. **`-march=` is the CPU optimization lever**
   - `-march=native` = compiler uses all available instructions
   - `-march=x86-64-v4` = compiler assumes AVX-512 baseline
   - `-march=x86-64-v3` = compiler assumes AVX2 baseline
   - This affects ALL code generation, not just SIMD-specific paths.

2. **`CMAKE_CUDA_ARCHITECTURES` controls GPU targeting**
   - Single arch (e.g., `120`) = smaller binary, optimized for one GPU
   - Multi arch (e.g., `80;86;89;90;120`) = larger binary, portable

3. **`GGML_NATIVE=ON` is the simplest native build**
   - Let llama.cpp handle everything with `-march=native`.
   - Don't fight it with explicit SIMD flags.

### Architecture Decisions (Why We Did What We Did)

1. **Unified build script (`build_llama_cpp.py`)**
   - Single source of truth for baremetal and Docker builds.
   - Docker calls the same script with `--target` and `--no-deps`.
   - Avoids drift between build environments.

2. **CPU mode validation with abort**
   - Prevents accidentally building suboptimal binaries.
   - Makes the "right thing" (--cpu-native) the path of least resistance.
   - CI can check exit code 2 for this specific failure.

3. **Removed legacy flags rather than keeping "for compatibility"**
   - No-op flags create false confidence and documentation debt.
   - If upstream removed them, we should too.

### If You're Investigating Performance Issues

1. **Check GPU utilization first** - if GPU is at 100%, CPU SIMD is irrelevant
2. **Verify actual binary contents** with `objdump -d ... | grep zmm` (AVX-512)
3. **Runtime dispatch is real** - llama.cpp prints system info showing active SIMD
4. **The build flags don't prevent SIMD** - they're more about compiler baseline
5. **Consider AVX2-focused builds** - for hybrid GPU+CPU workloads, AVX2 optimization may outperform
6. **Test both native and AVX2 builds** - use `--force` to compare performance on your specific workload

### Files to Understand

| File | Purpose |
|------|---------|
| `build_llama_cpp.py` | CLI entry point, arg parsing |
| `builder.py` | Build orchestration, CMake args assembly |
| `cmake_config.py` | Base CMake flag generation, CPU detection, validation |
| `config.py` | BuildConfig dataclass, CPUMode/GPUMode enums |

## References

- llama.cpp cmake: `vendor/llama.cpp/ggml/CMakeLists.txt`
- GGML CPU cmake: `vendor/llama.cpp/ggml/src/ggml-cpu/CMakeLists.txt`
- FindSIMD cmake: `vendor/llama.cpp/ggml/src/ggml-cpu/cmake/FindSIMD.cmake`
