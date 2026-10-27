# V2902A shared VLAN policy

Experimental native VLAN-policy extension for the V-SOL V2902A-XGS, vendor build
**241017**, AArch64/musl. LAN1's customer VLAN policy is authoritative and is applied
to both Ethernet ports. Physical/OMCI identities and GEM mappings remain separate.
Normal changes are incremental; initialization and failure recovery reconcile the
tables. This is not a background service or a startup rule-copy script.

**Not boot validated. Do not treat a successful build as approval to install.**
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
keep the host key elsewhere. Paramiko is optional and is needed only for this copy
step. The ignored `.venv/` and `vendor/` directories are never publication inputs.

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

- `build/libshared-vlan.so`: newly compiled extension, not a replacement libxpon.
- `build/libapi_omci_adpt.so.1.0.0`: locally patched owner-supplied adaptor.
- The adjacent JSON manifest: source and patched hashes, redirected imports.

The patcher never overwrites the input or an existing output. To rebuild the patch,
remove the generated patched adaptor explicitly, or use `make clean` and rebuild.
`make clean` removes only `build/`. Build, patch and test targets do not connect to a device or write flash.
Only the separately invoked `fetch-vendor` target reads files over SSH.

## How the local patch works

Eight undefined VLAN API imports in the supported adaptor are redirected to
matching wrappers in `libshared-vlan.so`, which is added as a dependency. Existing
adaptor machine instructions and defined exports remain unchanged; ELF symbol/hash
metadata and the writable segment's file representation change. Existing BSS is
preserved as zero-initialized memory.

The wrappers retain AArch64 argument ABI and signed `int` results (`0` success,
`-1` failure). TPID has separate input/output arguments, the DSCP map is 24 bytes,
and a rule is an opaque 64-byte little-endian record. Caller buffers are copied
when retained. Other ports pass through to stock routines. Policy effects and
failure conditions intentionally differ from independent-port operation; this is
not a claim of complete behavioral equivalence.

Decimal logical ports **11 and 12** correspond to physical LAN1 and LAN2 on this
build. These are not hexadecimal `0x11`/`0x12`, and are not OMCI UNI entity IDs.

## Integration and recovery gates

The two deployment files would occupy `/lib/libapi_omci_adpt.so.1.0.0` and
`/lib/libshared-vlan.so` in a device-specific image. Preserve the original adaptor
symlinks. **Do not copy build-only linker views or replace `libxpon.so.1`.**

This repository does not build a signed/flashable firmware image or modify a
bootloader. Use your board's supported image packaging and validation process;
do not concatenate these files into flash or bypass image/signature checks.

Before any deployment:

1. Verify device/build hashes and preserve recoverable original images, settings
   and board calibration outside Git. Confirm an independent recovery route.
2. Validate dependency resolution with the target loader without executing OMCI.
3. Audit all writers to the two VLAN tables. The incremental engine owns its
   canonical policy; direct external writes can invalidate that state. Remove
   conflicting startup workarounds only with a tested rollback plan.
4. Test real OMCI create/update/delete, global reset, process restart, fibre
   reconnection and reboot. A shell-only one-shot check is insufficient.
5. Confirm VLAN/GEM isolation and retain original files for rollback. Hardware
   writes are sequential, not atomic; rollback can itself fail. Failed rollback
   is reported as `-1`, with a diagnostic and counter, then retried before updates.

No automatic installation or flash target is supplied because these integration
and recovery gates are incomplete.

## Performance context

A preceding prototype was benchmarked on the ONT's Cortex-A53 CPU using real driver
operations in unused logical slots, never physical LAN1/LAN2: a two-port add/delete
cycle measured **30.81 us stock versus 31.41 us shared policy** (+1.95% median;
21 alternating batches of 500 cycles). Both paths used four driver writes. This
is configuration latency, not packet throughput, and not proof of identical speed.
The source-only validator/build packaging in this checkout needs its own release
benchmark; the earlier numbers must not be silently attributed to a changed build.

The previously discussed **30% slower add** was a separate experimental C
reconstruction that is not included or used here. This extension retains the
original vendor routines. [docs/contract.md](docs/contract.md) records the interface.

## Legal scope

This private pre-release repository has no public redistribution licence yet.
A public source licence can be selected separately; no rights to vendor software,
SDKs or firmware are granted. This is not represented as a clean-room implementation.
Interoperability knowledge was obtained by inspecting lawfully held software.

Spanish [Copyright Act, Article 100](https://www.boe.es/buscar/act.php?id=BOE-A-1996-8930#a100)
and EU [Directive 2009/24/EC, Article 6](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32009L0024)
provide conditional interoperability exceptions, not blanket redistribution
permission. Private visibility does not remove copyright, licence or other legal
obligations. Assess the facts and licences before public distribution; no legal
clearance for every jurisdiction is claimed. No signature-bypass code is included.
