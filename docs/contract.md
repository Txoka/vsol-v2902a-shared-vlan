# Interface and ownership

The target is Linux AArch64 ELF64, little endian, using the vendor musl ABI.
The wrappers return signed 32-bit `int`: zero for success and minus one for failure.

| Original import (wrapper adds `sp_`) | Arguments |
|---|---|
| `pon_add_vlan_rule` | unsigned port, pointer to readable 64-byte record |
| `pon_del_vlan_rule` | unsigned port, unsigned zero-based index |
| `pon_clean_vlan_rule` | unsigned port |
| `pon_clean_all_vlan_rule` | none |
| `pon_set_vlan_tpid` | unsigned port, unsigned input TPID, unsigned output TPID; low 16 bits used |
| `pon_set_vlan_dscp_map` | unsigned port, pointer to readable 24-byte map |
| `pon_set_vlan_downstream_mode` | unsigned port, unsigned mode in 0..9 |
| `pon_enable_default_vlan_rule` | unsigned port |

Valid caller buffers must remain readable for the call. Retained records/maps are
copied, with no ownership transfer. Defined vendor exports and unrelated-port
behavior are preserved. Diagnostic text and paired-port effects are not identical
to stock. A source-port operation can fail when the mirror or recovery fails.

LAN1's policy is canonical. Validated standalone LAN2 changes do not replace it.
Global reset still reaches the original global reset function and affects all
vendor ports. The engine serializes its own calls but cannot lock other processes
or arbitrary kernel writers. Those writers must not modify the paired VLAN state.

Normal updates apply the same operation to each port, then record the successful
change. Failure replays the previous canonical policy. A failed recovery keeps the
engine in recovery mode; new updates wait for restoration or return failure.
There is no atomic two-port hardware transaction and no persistent engine state;
OMCI must reconstruct policy after a process restart/reboot.

Host tests use independently written in-memory fakes, not vendor code or firmware.
They cover capacity, input validation, follower isolation, settings, deletion,
reset, concurrent calls, partial failure, rollback and later recovery. They do not
replace on-device OMCI/reset/boot validation.
