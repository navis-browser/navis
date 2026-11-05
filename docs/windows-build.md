# Windows builds and app-local runtime

The Windows x86_64 Release ZIP carries the Microsoft Visual C++ runtime beside `navis.exe`. Gecko's `--with-redist` discovers the toolchain's architecture-specific Redist directory, stages its configured runtime DLLs and includes them through the existing product manifest. The build does not install a machine-wide Redistributable or download individual DLLs.

`config/windows-runtime.json` pins the reviewed official DLL hashes and file version, records the toolchain Redist directory version separately, and enumerates external Windows libraries/API-set contracts. The directory version and DLL file version can differ. The configured-build gate verifies `WIN32_REDIST_DIR`, release CRT selection, the configured DLL names, their hashes, x64 architecture and dependencies before compilation. The package gate repeats the byte/architecture/import checks against the final application directory. It never resolves a missing VC DLL through the build host's system installation, Wine prefix, PATH or another package.

The verifier uses the LLVM toolchain's `llvm-readobj` to inspect normal and delay imports in every packaged EXE/DLL, including the pinned runtime DLLs. It checks each dependency against the same application directory or the explicit system list. Unknown dependencies, unreviewed runtime bytes, nested PE files, case-colliding filenames, symlinks and packaged system-library replacements fail. `NAVIS_LLVM_READOBJ` can select an explicit executable; otherwise the verifier uses PATH or the bootstrapped LLVM location.

```sh
python3 scripts/verify-windows-runtime.py --objdir ../runtime/gecko/obj-navis-win64-release
python3 scripts/verify-windows-runtime.py --runtime /path/to/extracted/navis --json
python3 -m unittest discover -s tests -p test_windows_runtime.py
```

## Toolchain and Redist updates

When updating the Windows toolchain, obtain redistributable files from Microsoft's official toolchain Redist directory. Verify each selected file's Microsoft Authenticode signature on Windows, architecture and file version; retain that evidence with the build inputs. Update the policy's exact hashes/version and directory provenance together with the toolchain review. The configured gate deliberately rejects unreviewed replacements. Use a Redistributable version compatible with the selected compiler, as described by [Microsoft](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).

Reinspect normal and delay imports after changing the compiler, Gecko or any bundled library. Add a system-library name only after establishing that it belongs to Windows and reviewing availability/fallback behavior on supported OS versions. API-set names describe loader contracts and do not necessarily identify files on disk; a name prefix alone does not establish availability. Keep the [Windows API-set](https://learn.microsoft.com/en-us/windows/win32/apiindex/windows-apisets) and [DLL search-order](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order) rules in the review.

App-local libraries are maintained through Navis releases. Each release reruns the configured-build and final-package gates; toolchain/Redist security updates require a newly verified package. Preserve the applicable Microsoft distribution terms and notices; see [Microsoft's redistribution documentation](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files).

## Native acceptance

Run the extracted package on a clean supported Windows installation without Visual Studio or a machine-wide VC Redistributable. Verify startup, local navigation, content processes, sandbox/recovery and genuine same-Profile application relaunch. Record the OS, exact package/source identity and module paths showing that the VC runtime is loaded from the package. Do not uninstall shared system runtimes from a user's machine to manufacture this environment.

Import checks establish the declared static dependency boundary. They do not prove every imported symbol, dynamic `LoadLibrary` path, optional OS contract or Windows version works. A native run on a preconfigured machine is useful but cannot substitute for the clean-system test. Record missing native environments as pending; a build/package result alone is not clean-system acceptance.
