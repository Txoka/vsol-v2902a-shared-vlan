.DEFAULT_GOAL := all
CLANG ?= clang
HOST_CC ?= clang
PYTHON ?= python3
VENDOR_LIB_DIR ?= vendor
VENDOR_ADAPTOR ?=
ONT_HOST ?= 192.168.101.1
ONT_USER ?= admin
KNOWN_HOSTS ?= $(HOME)/.ssh/known_hosts
BUILD := build
SOURCES := src/policy.c src/vendor_io.c src/validate.c
TARGET_FLAGS := --target=aarch64-linux-musl -fuse-ld=lld -flto -O2 \
 -ffreestanding -fno-builtin -fPIC -fstack-protector-strong \
 -ffunction-sections -Wall -Wextra -Werror

.PHONY: all test patch clean help check-vendor fetch-vendor
all: $(BUILD)/libshared-vlan.so

fetch-vendor:
	$(PYTHON) tools/fetch_vendor.py --host "$(ONT_HOST)" --user "$(ONT_USER)" \
	 --known-hosts "$(KNOWN_HOSTS)" --output "$(VENDOR_LIB_DIR)"

$(BUILD):
	mkdir -p "$@"

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

$(BUILD)/libshared-vlan.so: $(SOURCES) $(BUILD)/link-views/.stamp
	$(CLANG) $(TARGET_FLAGS) -nostdlib -shared \
	 -Wl,-soname,libshared-vlan.so -Wl,-z,defs -Wl,--gc-sections \
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

clean:
	rm -rf -- "$(BUILD)"

help:
	@echo 'make test                         Host tests; no vendor files or device needed'
	@echo 'make fetch-vendor ONT_HOST=...     Prompted SSH copy from the ONT; needs Paramiko'
	@echo 'make VENDOR_LIB_DIR=/local/libs    Compile the ARM64 extension'
	@echo 'make patch VENDOR_LIB_DIR=/local/libs VENDOR_ADAPTOR=/local/original.so'
	@echo 'No target uploads, installs, flashes, restarts, or downloads firmware.'
