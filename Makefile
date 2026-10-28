.DEFAULT_GOAL := all
CLANG ?= clang
HOST_CC ?= clang
PYTHON ?= python3
VENDOR_LIB_DIR ?= vendor
VENDOR_ADAPTOR ?= $(VENDOR_LIB_DIR)/libapi_omci_adpt.so.1.0.0
ONT_HOST ?= 192.168.101.1
ONT_USER ?= admin
KNOWN_HOSTS ?= $(HOME)/.ssh/known_hosts
ROUTER_HOST ?=
MAX_BENCH_RATIO ?= 1.05
STOCK_IMAGE ?= vendor/stock-tclinux.bin
UNSQUASHFS ?= build/squashfs-source/squashfs-tools/unsquashfs
MKSQUASHFS ?= build/squashfs-source/squashfs-tools/mksquashfs
BUILD := build
SOURCES := src/policy.c src/vendor_io.c src/validate.c
TARGET_FLAGS := --target=aarch64-linux-musl -fuse-ld=lld -flto -O2 \
 -ffreestanding -fno-builtin -fPIC -fstack-protector-strong \
 -ffunction-sections -Wall -Wextra -Werror

.PHONY: all test patch clean help check-vendor fetch-vendor check update device-tools test-device test-integration device-rollback device-preflight benchmark-device check-binary fetch-image squashfs-tools image validate-device install-device
all: $(BUILD)/libsp.so

fetch-vendor:
	$(PYTHON) tools/fetch_vendor.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --output "$(VENDOR_LIB_DIR)"

$(BUILD):
	mkdir -p -m 700 "$@"

check-vendor:
	@test -f "$(VENDOR_LIB_DIR)/libxpon.so.1" || \
	 (echo 'Provide your own libxpon.so.1 using VENDOR_LIB_DIR=/path/to/local/libraries'; exit 1)
	@test -f "$(VENDOR_LIB_DIR)/libc.so" || \
	 (echo 'Provide the matching ONT musl libc.so in VENDOR_LIB_DIR'; exit 1)

$(BUILD)/link-views/.stamp: check-vendor tools/link_view.py tools/elf64.py | $(BUILD)
	mkdir -p "$(BUILD)/link-views"
	$(PYTHON) tools/link_view.py "$(VENDOR_LIB_DIR)/libxpon.so.1" "$(BUILD)/link-views/libxpon.so.1"
	$(PYTHON) tools/link_view.py "$(VENDOR_LIB_DIR)/libc.so" "$(BUILD)/link-views/libc.so"
	touch "$@"

$(BUILD)/libsp.so: $(SOURCES) $(BUILD)/link-views/.stamp
	$(CLANG) $(TARGET_FLAGS) -nostdlib -shared \
	 -Wl,-soname,libsp.so -Wl,-z,defs -Wl,--gc-sections \
	 -Wl,-z,relro,-z,now $(SOURCES) -L"$(BUILD)/link-views" \
	 -l:libxpon.so.1 -l:libc.so -o "$@"

test: | $(BUILD)
	$(HOST_CC) -std=c11 -O1 -g -Wall -Wextra -Werror \
	 -fsanitize=address,undefined -pthread src/policy.c src/validate.c \
	 tests/test_policy.c -o "$(BUILD)/test-policy"
	"$(BUILD)/test-policy"
	$(HOST_CC) -std=c11 -O1 -g -Wall -Wextra -Werror \
	 -fsanitize=address,undefined src/validate.c tests/test_validate.c \
	 -o "$(BUILD)/test-validate"
	"$(BUILD)/test-validate"
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

patch: all
	@test -n "$(VENDOR_ADAPTOR)" || \
	 (echo 'Set VENDOR_ADAPTOR=/path/to/original/libapi_omci_adpt.so.1.0.0'; exit 1)
	$(PYTHON) tools/patch_adaptor.py "$(VENDOR_ADAPTOR)" \
	 "$(BUILD)/libapi_omci_adpt.so.1.0.0"

$(BUILD)/query-rules: device/query_rules.c | $(BUILD)
	$(CLANG) --target=aarch64-linux-musl -fuse-ld=lld -O2 -nostdlib -static \
	 -ffreestanding -fno-builtin -fno-stack-protector -Wall -Wextra -Werror "$<" -o "$@"

device-tools: all $(BUILD)/query-rules
	$(CLANG) $(TARGET_FLAGS) -DSOURCE=17u -DFOLLOWER=18u -nostdlib -shared \
	 -Wl,-soname,libshared-vlan-bench.so -Wl,-z,defs -Wl,--gc-sections \
	 -Wl,-z,relro,-z,now $(SOURCES) -L"$(BUILD)/link-views" \
	 -l:libxpon.so.1 -l:libc.so -o "$(BUILD)/libshared-vlan-bench.so"
	$(CLANG) --target=aarch64-linux-musl -fuse-ld=lld -O2 -ffreestanding \
	 -fno-builtin -fno-stack-protector -nostdlib \
	 -Wl,--dynamic-linker=/lib/ld-musl-aarch64.so.1 device/benchmark.c \
	 -L"$(BUILD)" -l:libshared-vlan-bench.so -L"$(BUILD)/link-views" \
	 -l:libxpon.so.1 -l:libc.so -o "$(BUILD)/benchmark-real-driver"

check-binary: patch
	$(PYTHON) tools/check_binary.py --source "$(VENDOR_ADAPTOR)" --build "$(BUILD)"

check: test check-binary device-tools

update: check
	$(PYTHON) tools/package_update.py --build "$(BUILD)" --require-device-tests --max-ratio "$(MAX_BENCH_RATIO)"

validate-device: check
	$(PYTHON) tools/device_benchmark.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" --max-ratio "$(MAX_BENCH_RATIO)"
	$(PYTHON) tools/device_test.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" \
	 $(if $(ROUTER_HOST),--router-host "$(ROUTER_HOST)") --phase all
	$(PYTHON) tools/package_update.py --build "$(BUILD)" --require-device-tests --max-ratio "$(MAX_BENCH_RATIO)"

fetch-image:
	$(PYTHON) tools/fetch_image.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --output "$(STOCK_IMAGE)"

squashfs-tools:
	$(PYTHON) tools/setup_squashfs.py

image: update
	$(PYTHON) tools/build_image.py --stock "$(STOCK_IMAGE)" --build "$(BUILD)" \
	 --unsquashfs "$(UNSQUASHFS)" --mksquashfs "$(MKSQUASHFS)"

test-device: check
	$(PYTHON) tools/device_test.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" \
	 $(if $(ROUTER_HOST),--router-host "$(ROUTER_HOST)") --phase all

device-preflight: check
	$(PYTHON) tools/device_test.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" \
	 $(if $(ROUTER_HOST),--router-host "$(ROUTER_HOST)") --phase preflight

benchmark-device: check
	$(PYTHON) tools/device_benchmark.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" --max-ratio "$(MAX_BENCH_RATIO)"

test-integration: check
	$(PYTHON) tools/device_test.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" \
	 $(if $(ROUTER_HOST),--router-host "$(ROUTER_HOST)") --phase integration

device-rollback:
	$(PYTHON) tools/device_test.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" \
	 $(if $(ROUTER_HOST),--router-host "$(ROUTER_HOST)") --phase rollback

install-device: update
	@test -n "$(ROUTER_HOST)" || (echo 'Set ROUTER_HOST for WAN health verification'; exit 1)
	$(PYTHON) tools/install_device.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --build "$(BUILD)" --router-host "$(ROUTER_HOST)"

clean:
	rm -rf -- "$(BUILD)"

help:
	@echo 'make test                         Host tests; no vendor files or device needed'
	@echo 'make fetch-vendor ONT_HOST=...     Prompted SSH copy from the ONT; needs Paramiko'
	@echo 'make VENDOR_LIB_DIR=/local/libs    Compile the ARM64 extension'
	@echo 'make patch VENDOR_LIB_DIR=/local/libs VENDOR_ADAPTOR=/local/original.so'
	@echo 'make update                       Local tests/build/native bundle; no device changes'
	@echo 'make test-device                  Guarded OMCI restart + reboot + restore to stock'
	@echo 'make device-rollback              Request immediate stock restoration'
	@echo 'make install-device ROUTER_HOST=... Validated persistent overlay install, guarded reboot'
