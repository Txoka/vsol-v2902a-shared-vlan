# V2902A shared VLAN policy

## Why this patch exists

On a V-SOL V2902A-XGS running vendor build **241017**, DIGI PPPoE over VLAN 20
worked through the 1G LAN1 port, while the faster LAN2/10GE port established a
2.5G Ethernet link but received no PPPoE PADO replies. That prevented using the
faster port for the Internet connection despite its working physical link.

Inspection of the hardware VLAN tables found the difference: LAN1 had the
VLAN-20 transparent service rule, while LAN2 lacked it. Both ports already had
the required GEM mappings. Adding the missing LAN2 rule restored service, but a
one-time change was lost on reboot. Linux bridge membership alone did not describe
the vendor's separately programmed PON hardware forwarding policy.

This patch makes **LAN1's customer VLAN policy authoritative for both ports**.
It intercepts the vendor adaptor's VLAN API calls so additions, deletions, clears
and supported VLAN settings update the paired policy, while initialization and
failure recovery reconcile the hardware tables. Physical Ethernet identities,
OMCI identities and GEM mappings remain separate. It replaces the boot-time
VLAN-20 rule-copy workaround with native policy handling inside the OMCI process.

This is an intentional shared-service policy for installations where both Ethernet
ports should carry the same customer service. It is **not suitable for deployments
that require independent services or isolation between LAN1 and LAN2**. The evidence
identifies the missing hardware rule; it does not establish whether the original
asymmetry came from an OLT provisioning choice, the vendor adaptor, or their
interaction. This is not claimed to fix every V2902A or every ISP configuration.

## Supported firmware and validation

The extension targets vendor build **241017**, AArch64/musl, and retains the
original vendor hardware routines. Normal policy changes are incremental; this is
not a background service or a startup rule-copy script.

The native overlay has passed a guarded real-device OMCI restart, reboot, WAN and
rollback test on the supported build. Reproduce the gates for your exact binaries
before installing. A repacked full firmware image is a separate, unbooted candidate.
The currently supported adaptor has SHA-256:

```text
05bd0a0b82bdfd5c910878069b8ef68a07ea87b4b591fea6dbcdddc4b5de0726
```

The patcher rejects every other build. No force mode is provided.

## What this repository contains

Only original extension source, tests, a Makefile and local ELF build/patch tools.
It contains no vendor firmware, libraries, SDK, device credentials, captures,
decompiler output, reconstructed vendor functions, or flash images. Generated
files stay in ignored `build/`; optional owner-supplied inputs belong in ignored
`vendor/` or outside this checkout. Do not commit those files, even to a private fork.

## Requirements and vendor functions

- Linux host, GNU Make, Python 3.10+, Clang and LLD with AArch64 support.
- Host Clang and sanitizer runtime for `make test`.
- Your own matching `libxpon.so.1` and `libc.so`, obtained lawfully from your device
  or matching SDK. Use the same vendor build and architecture, not your host libc.
- Your original `libapi_omci_adpt.so.1.0.0` when applying the patch.

The repository supplies the observed function declarations and rule layout needed
to compile our code. **It does not recompile the vendor functions.** The link step
checks our imports against the exports of your local vendor libraries, using
`-z defs`; the extension calls the original routines on the ONT at runtime.

The vendor DSOs lack ELF section headers. `tools/link_view.py` adds section metadata
to private build-only copies so LLD can read their exported symbols. The originals
are not modified. Never install these link-view copies.

## Build and test

### Copy directly from your ONT

There is no verified public download configured for these exact libraries. Copy
the originals from your own ONT rather than downloading unknown firmware.
The helper reads only the three required files from `/rom/lib`, verifies remote
SHA-256 checksums and the supported adaptor build, and refuses to overwrite local
files. It never writes anything to the ONT. It supports the forced interactive
shell used by this firmware, where ordinary SCP/SFTP may fail.

First connect normally, verify the SSH host fingerprint independently and record
the host key in your usual SSH known-hosts file. Exit that SSH session afterward:

```sh
ssh admin@192.168.101.1
```

Then, from this checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install paramiko==5.1.0
make fetch-vendor PYTHON=.venv/bin/python ONT_HOST=192.168.101.1 ONT_USER=admin
make test
make patch VENDOR_LIB_DIR=vendor \
  VENDOR_ADAPTOR=vendor/libapi_omci_adpt.so.1.0.0
```

The password is prompted, never included in a command or saved. Unknown or changed
host keys are rejected. Override `KNOWN_HOSTS=/path/to/verified/known_hosts` if you
keep the host key elsewhere. Paramiko is needed for the SSH copy, hardware tests and installation. The ignored `.venv/` and `vendor/` directories are never publication inputs.

### Use copies you already have

Arrange local inputs without adding them to Git:

```text
/path/to/local/vendor-libraries/
  libxpon.so.1
  libc.so
```

```sh
make test
make VENDOR_LIB_DIR=/path/to/local/vendor-libraries
make patch VENDOR_LIB_DIR=/path/to/local/vendor-libraries \
  VENDOR_ADAPTOR=/path/to/original/libapi_omci_adpt.so.1.0.0
```

Outputs:

- `build/libsp.so`: newly compiled extension, not a replacement libxpon.
- `build/libapi_omci_adpt.so.1.0.0`: locally patched owner-supplied adaptor.
- The adjacent JSON manifest: source and patched hashes, redirected imports.

The patcher never overwrites the input or a differing existing output. To rebuild the patch,
remove the generated patched adaptor explicitly, or use `make clean` and rebuild.
`make clean` removes only `build/`. The plain `test`, `patch` and `check` targets are local. Explicit device targets
connect over SSH; the integration and installation targets change overlay files
and restart/reboot the ONT. They never write a firmware bank.

## How the local patch works

Eight undefined VLAN API imports change from the `pon_` prefix to equal-length
`spn_` aliases. The existing `libxpon.so` dependency becomes `libsp.so`; our
extension depends on the original `libxpon.so.1`. Only import strings and the SysV
symbol hash table change. The adaptor keeps its exact length, program headers,
machine instructions, defined exports and BSS layout. No appended string table or
expanded segment is needed. The aliases share function addresses with the test API.

The wrappers retain AArch64 argument ABI and signed `int` results (`0` success,
`-1` failure). TPID has separate input/output arguments, the DSCP map is 24 bytes,
and a rule is an opaque 64-byte little-endian record. Caller buffers are copied
when retained. Other ports pass through to stock routines. Policy effects and
failure conditions intentionally differ from independent-port operation; this is
not a claim of complete behavioral equivalence.

Decimal logical ports **11 and 12** correspond to physical LAN1 and LAN2 on this
build. These are not hexadecimal `0x11`/`0x12`, and are not OMCI UNI entity IDs.

## Real-device validation and installation

The following commands prompt for the ONT password and require a verified SSH host
key. Router health uses your existing root SSH key; no credentials are stored.
Substitute your addresses. Allow brief WAN interruptions during OMCI restarts and
reboot. Keep the working cable connected throughout.

```sh
make validate-device PYTHON=.venv/bin/python \
  ONT_HOST=192.168.101.1 ROUTER_HOST=192.168.1.1
make install-device PYTHON=.venv/bin/python \
  ONT_HOST=192.168.101.1 ROUTER_HOST=192.168.1.1
```

`validate-device` runs host tests and exact binary checks, the real-driver
benchmark, OMCI restart, real reboot, WAN health and stock rollback, then prepares
`build/V2902A-241017-native-update.tar.gz`. It deliberately leaves stock installed.
Tests use unused decimal slots 17/18 for benchmarking, require empty initial slots,
and verify both physical port tables are unchanged afterward. The performance gate
is a maximum median configuration latency ratio of 1.05; it is not relaxed on failure.

`install-device` refuses missing, failed or different-build evidence. It backs up
the stock adaptor, old startup settings and workaround under the private device
recovery directory `/root/vsol-shared-vlan-test`. A temporary early-boot procd
rollback guard is armed before activation; loss of host keepalives for five minutes
restores stock without depending on the patched library. After a successful OMCI
restart and real reboot with WAN healthy and both VLAN policies equal, installation
commits the overlay files and removes the temporary guard and old rule-copy helper.
The backups remain. This is not a permanent background service.

The installed files are `/lib/libapi_omci_adpt.so.1.0.0` and `/lib/libsp.so`.
Existing adaptor symlinks remain. Never install linker-view copies or replace
`libxpon.so.1`. OMCI loads the extension normally at runtime and boot. The overlay
survives ordinary reboot, but factory reset removes it and firmware upgrades can
replace it. It is operationally native, but it is not part of the immutable ROM.

To restore the verified original setup, including its previous VLAN-20 workaround:

```sh
make device-rollback PYTHON=.venv/bin/python \
  ONT_HOST=192.168.101.1 ROUTER_HOST=192.168.1.1
```

Individual gates are available as `make test`, `make check-binary`,
`make benchmark-device`, `make test-integration`, and `make test-device`.
Device reports and exact binary hashes remain in ignored `build/`. Host tests
cover create/update/delete/reset, validation, concurrency and failure recovery;
real-device tests cover actual OMCI initialization, process restart and reboot.
These do not constitute exhaustive testing of every possible OLT transaction,
fibre re-registration or every ISP configuration. External direct writers must
not modify the paired VLAN tables behind the engine.

## Optional full firmware candidate

This is not required for the persistent overlay installation. To prepare a local
candidate from your device's own stock bank, after the native validation gates:

```sh
make fetch-image PYTHON=.venv/bin/python ONT_HOST=192.168.101.1
make squashfs-tools
make image
```

The filesystem tools are built from a pinned official SquashFS-tools revision.
The image builder preserves every existing filesystem entry and its metadata
except the patched adaptor and adds only `lib/libsp.so`. It preserves kernel,
DTB, FIP and container layout, verifies the original CRC/kernel hash, regenerates
the container CRC, and rejects authenticated filesystem layouts. It uses 512 KiB
XZ blocks instead of 256 KiB to fit the original rootfs budget.

**The full image is structurally verified but has not been flashed or boot-tested.**
No automatic flashing target is provided. A successful native overlay reboot does
not prove that a repacked filesystem boots. Preserved overlay files can also mask
new ROM files after a flash; do not assume an upgrade retaining settings provides
a clean installation. No image or vendor library belongs in Git.

## Size and performance

For the current size-neutral design: original and patched adaptors are both
1,408,720 bytes; the extension is 12,848 bytes. Combined disk growth is **0.91%**.
Additional summed ELF load-segment memory is 14,389 bytes, before page rounding
and stack usage. `make check-binary` verifies these properties for the actual build.

The latest real-device benchmark measured **30.74432 us stock versus 31.40208 us
shared policy**, **+2.14% median**, over 21 alternating batches of 500 two-port
add/delete cycles on one CPU. Both paths perform four driver writes. Stock/shared
p95 was 31.56704/32.89048 us. This is configuration latency, not packet throughput.
Reports are tied to exact binary hashes; reproduce the benchmark for changed code.

The previously discussed **30% slower add** was a separate experimental C
reconstruction that is not included or used here. The extension retains the
original vendor routines. [docs/contract.md](docs/contract.md) records the interface.

## Legal scope

The source code and documentation in this repository are licensed under the
[MIT License](LICENSE). That licence covers this project's code, not vendor
firmware, libraries, SDKs or locally generated files containing vendor code.
Users must supply their own lawfully obtained vendor inputs; no such inputs or
patched vendor binaries are distributed here. This is not represented as a
clean-room implementation. Interoperability knowledge was obtained by inspecting
lawfully held software.

Spanish [Copyright Act, Article 100](https://www.boe.es/buscar/act.php?id=BOE-A-1996-8930#a100)
and EU [Directive 2009/24/EC, Article 6](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32009L0024)
provide conditional interoperability exceptions, not blanket redistribution
permission. Assess the applicable facts and licences before redistributing vendor
material; no legal clearance for every jurisdiction is claimed. No signature-bypass
code is included.
