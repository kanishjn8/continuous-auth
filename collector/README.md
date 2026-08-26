# Native collector skeleton

The collector is C++17 and consumes only the generated protocol header. T-001/T-004
own real Windows hook and timing behavior; this T-002 target intentionally performs no
input capture.

```powershell
cmake -S collector -B build/collector -DBUILD_TESTING=ON
cmake --build build/collector --config Release
ctest --test-dir build/collector -C Release --output-on-failure
```

A C++ compiler and CMake are required. The global-hook feasibility spike must be run
on real target hardware and reviewed before collector implementation is accepted.



