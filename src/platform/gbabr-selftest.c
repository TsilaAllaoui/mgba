/* Protocol/integration self-test for the mGBA-GBABR virtual cartridge.
 *
 * Tests command sequences through mCore::busWrite16/busRead16, i.e. the same
 * GBA memory path guest ARM/Thumb code uses. This intentionally does not call
 * the private NOR command handlers directly.
 */
#include <mgba/core/config.h>
#include <mgba/core/core.h>
#include <mgba/core/serialize.h>
#include <mgba/gba/core.h>
#include <mgba/internal/gba/cart/gbabr.h>
#include <mgba/internal/gba/cart/unlicensed.h>
#include <mgba/internal/gba/gba.h>
#include <mgba-util/vfs.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ROM_BASE 0x08000000u
#define SRAM_BASE 0x0E000000u
#define TEST_ROM_BYTES (1024u * 1024u)

struct TestCore {
	struct mCore* core;
	struct GBA* gba;
	struct GBAGBABRCart* cart;
};

static int gFailures;

static void _result(const char* name, bool pass) {
	printf("%-42s %s\n", name, pass ? "PASS" : "FAIL");
	if (!pass) ++gFailures;
}

static void _clearEnv(void) {
	unsetenv("MGBA_GBAVC_PROFILE");
	unsetenv("MGBA_GBAVC_STRICT");
	unsetenv("MGBA_GBAVC_ALLOW");
	unsetenv("MGBA_GBAVC_ROM_OFFSET");
	unsetenv("MGBA_GBAVC_NOR");
	unsetenv("MGBA_GBAVC_FRAM");
	unsetenv("MGBA_GBAVC_EVENTS");
	unsetenv("MGBA_GBAVC_NATIVE_SAVE_ALLOWED");
	unsetenv("MGBA_GBAVC_EXISTING");
	unsetenv("MGBA_GBABR_PROFILE");
	unsetenv("MGBA_GBABR_STRICT");
	unsetenv("MGBA_GBABR_ALLOW");
	unsetenv("MGBA_GBABR_ROM_OFFSET");
	unsetenv("MGBA_GBABR_NOR");
	unsetenv("MGBA_GBABR_FRAM");
	unsetenv("MGBA_GBABR_EVENTS");
	unsetenv("MGBA_GBABR_NATIVE_SAVE_ALLOWED");
}

static bool _newCoreConfigured(struct TestCore* t, const char* profile, const char* allow, const char* romOffset, bool strict,
                               const char* norPath, const char* framPath, bool existing) {
	memset(t, 0, sizeof(*t));
	_clearEnv();
	setenv("MGBA_GBAVC_PROFILE", profile, 1);
	setenv("MGBA_GBAVC_STRICT", strict ? "1" : "0", 1);
	if (allow) setenv("MGBA_GBAVC_ALLOW", allow, 1);
	if (romOffset) setenv("MGBA_GBAVC_ROM_OFFSET", romOffset, 1);
	if (norPath) setenv("MGBA_GBAVC_NOR", norPath, 1);
	if (framPath) setenv("MGBA_GBAVC_FRAM", framPath, 1);
	if (existing) setenv("MGBA_GBAVC_EXISTING", "1", 1);

	uint8_t* rom = malloc(TEST_ROM_BYTES);
	if (!rom) return false;
	memset(rom, 0xFF, TEST_ROM_BYTES);
	rom[0] = 0xFE; rom[1] = 0xFF; rom[2] = 0xFF; rom[3] = 0xEA;
	struct VFile* vf = VFileMemChunk(rom, TEST_ROM_BYTES);
	free(rom);
	if (!vf) return false;

	t->core = GBACoreCreate();
	if (!t->core) { vf->close(vf); return false; }
	if (!t->core->init(t->core)) { vf->close(vf); t->core->deinit(t->core); t->core = NULL; return false; }
	mCoreInitConfig(t->core, "gbabr-selftest");
	mCoreConfigSetDefaultValue(&t->core->config, "idleOptimization", "remove");
	if (!t->core->loadROM(t->core, vf)) {
		vf->close(vf);
		mCoreConfigDeinit(&t->core->config);
		t->core->deinit(t->core);
		t->core = NULL;
		return false;
	}
	t->core->reset(t->core);
	t->gba = t->core->board;
	if (!t->gba || t->gba->memory.unl.type != GBA_UNL_CART_GBABR || !t->gba->memory.unl.gbabr) return false;
	t->cart = t->gba->memory.unl.gbabr;
	return true;
}

static bool _newCore(struct TestCore* t, const char* profile, const char* allow, const char* romOffset, bool strict) {
	return _newCoreConfigured(t, profile, allow, romOffset, strict, NULL, NULL, false);
}

static void _freeCore(struct TestCore* t) {
	if (!t->core) return;
	t->core->unloadROM(t->core);
	mCoreConfigDeinit(&t->core->config);
	t->core->deinit(t->core);
	memset(t, 0, sizeof(*t));
	_clearEnv();
}

static void _w16(struct TestCore* t, uint32_t local, uint16_t value) {
	t->core->busWrite16(t->core, ROM_BASE + local, value);
}

static void _intelUnlock(struct TestCore* t, uint32_t local) {
	_w16(t, local, 0x0050);
	_w16(t, local, 0x0060);
	_w16(t, local, 0x00D0);
	_w16(t, local, 0x00FF);
}

static void _m6Program(struct TestCore* t, uint32_t local, uint16_t value) {
	_intelUnlock(t, local);
	_w16(t, local, 0x0050);
	_w16(t, local, 0x0040);
	_w16(t, local, value);
	_w16(t, local, 0x00FF);
}

static void _intelErase(struct TestCore* t, uint32_t local) {
	_intelUnlock(t, local);
	_w16(t, local, 0x0050);
	_w16(t, local, 0x0020);
	_w16(t, local, 0x00D0);
	_w16(t, local, 0x00FF);
}

static void _amdProgram(struct TestCore* t, uint32_t local, uint16_t value) {
	_w16(t, 0xAAA, 0x00AA);
	_w16(t, 0x554, 0x0055);
	_w16(t, 0xAAA, 0x00A0);
	_w16(t, local, value);
}

static void _s29Erase(struct TestCore* t, uint32_t local) {
	_w16(t, 0xAAA, 0x00AA);
	_w16(t, 0x554, 0x0055);
	_w16(t, 0xAAA, 0x0080);
	_w16(t, 0xAAA, 0x00AA);
	_w16(t, 0x554, 0x0055);
	_w16(t, local, 0x0030);
}

static bool _countAtLeast(struct TestCore* t, enum GBAGBABREventType type, uint64_t n) {
	const struct GBAGBABREventStats* s = GBAGBABRGetEventStats(t->cart);
	return s && s->counts[type] >= n;
}

static void _testM6(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m6m", "0x80000:0x10000", NULL, true);
	_result("M6/M6M activate", ok);
	if (!ok) return;
	_m6Program(&t, 0x80000, 0xA55A);
	_result("M6/M6M word40 program", GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x5A && GBAGBABRPhysicalNOR(t.cart)[0x80001] == 0xA5 && _countAtLeast(&t, GBA_GBABR_EVENT_NOR_PROGRAM, 1));
	_intelErase(&t, 0x80000);
	_result("M6/M6M 64K erase", GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0xFF && GBAGBABRPhysicalNOR(t.cart)[0x80001] == 0xFF && _countAtLeast(&t, GBA_GBABR_EVENT_NOR_ERASE, 1));
	_freeCore(&t);
}

static void _testM28(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m28", "0x80000:0x10000", NULL, true);
	_result("M28 activate", ok);
	if (!ok) return;
	_m6Program(&t, 0x80000, 0x1234);
	_result("M28 legal word40 program", GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x34 && GBAGBABRPhysicalNOR(t.cart)[0x80001] == 0x12);
	_m6Program(&t, 0x00000, 0xA55A);
	_result("M28 parameter/boot range protected", _countAtLeast(&t, GBA_GBABR_EVENT_ILLEGAL_NOR_WRITE, 1) && GBAGBABRPhysicalNOR(t.cart)[0] != 0x5A);
	_freeCore(&t);
}

static void _testM36(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m36", "0x200000:0x20000", NULL, true);
	_result("M36 activate", ok);
	if (!ok) return;
	uint32_t a = 0x200000;
	_intelUnlock(&t, a);
	_w16(&t, a, 0x0050);
	_w16(&t, a, 0x00E8);
	_w16(&t, a, 15);
	for (unsigned i = 0; i < 16; ++i) _w16(&t, a + i * 2, (uint16_t) (0xA000u + i));
	_w16(&t, a, 0x00D0);
	_w16(&t, a, 0x00FF);
	bool dataOk = true;
	for (unsigned i = 0; i < 16; ++i) {
		uint16_t got = (uint16_t) GBAGBABRPhysicalNOR(t.cart)[a + i * 2] | (uint16_t) (GBAGBABRPhysicalNOR(t.cart)[a + i * 2 + 1] << 8);
		if (got != (uint16_t) (0xA000u + i)) dataOk = false;
	}
	_result("M36 E8 32-byte buffered program", dataOk && _countAtLeast(&t, GBA_GBABR_EVENT_NOR_PROGRAM, 16));
	_intelErase(&t, a);
	_result("M36 128K erase", GBAGBABRPhysicalNOR(t.cart)[a] == 0xFF && _countAtLeast(&t, GBA_GBABR_EVENT_NOR_ERASE, 1));
	_freeCore(&t);
}

static void _testD137(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m6mgd137", "0x200000:0x10000", NULL, true);
	_result("M6MGD137 activate", ok);
	if (!ok) return;
	_m6Program(&t, 0x200000, 0xBEEF);
	_result("M6MGD137 relative word40 program", GBAGBABRPhysicalNOR(t.cart)[0x200000] == 0xEF && GBAGBABRPhysicalNOR(t.cart)[0x200001] == 0xBE);
	_intelErase(&t, 0x200000);
	_result("M6MGD137 64K erase", GBAGBABRPhysicalNOR(t.cart)[0x200000] == 0xFF && _countAtLeast(&t, GBA_GBABR_EVENT_NOR_ERASE, 1));
	_freeCore(&t);
}

static void _testMX26(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "mx26", "0x100000:0x10000", NULL, true);
	_result("MX26 activate", ok);
	if (!ok) return;
	_amdProgram(&t, 0x100000, 0xCAFE);
	_result("MX26 AMD unlock-word program", GBAGBABRPhysicalNOR(t.cart)[0x100000] == 0xFE && GBAGBABRPhysicalNOR(t.cart)[0x100001] == 0xCA && _countAtLeast(&t, GBA_GBABR_EVENT_NOR_PROGRAM, 1));
	/* MX26 profile intentionally has no runtime erase. */
	_s29Erase(&t, 0x100000);
	_result("MX26 runtime erase unavailable", !_countAtLeast(&t, GBA_GBABR_EVENT_NOR_ERASE, 1));
	_freeCore(&t);
}

static void _testS29(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "s29", "0x2000000:0x40000", "0x2000000", true);
	_result("S29/Mini128 activate", ok);
	if (!ok) return;
	/* Local 0 maps to physical 32 MiB because rom_offset selects bank 1. */
	_amdProgram(&t, 0x000100, 0x55AA);
	_result("S29 mapped single-word program", GBAGBABRPhysicalNOR(t.cart)[0x2000100] == 0xAA && GBAGBABRPhysicalNOR(t.cart)[0x2000101] == 0x55);

	uint32_t a = 0x20000;
	_w16(&t, 0xAAA, 0x00AA); _w16(&t, 0x554, 0x0055); _w16(&t, a, 0x0025);
	_w16(&t, a, 3);
	for (unsigned i = 0; i < 4; ++i) _w16(&t, a + i * 2, (uint16_t) (0x3300u + i));
	_w16(&t, a, 0x0029);
	bool buffered = true;
	for (unsigned i = 0; i < 4; ++i) {
		uint32_t p = 0x2000000u + a + i * 2;
		uint16_t got = (uint16_t) GBAGBABRPhysicalNOR(t.cart)[p] | (uint16_t) (GBAGBABRPhysicalNOR(t.cart)[p + 1] << 8);
		if (got != (uint16_t) (0x3300u + i)) buffered = false;
	}
	_result("S29 write-buffer program", buffered);
	_s29Erase(&t, a);
	_result("S29 128K sector erase", GBAGBABRPhysicalNOR(t.cart)[0x2020000] == 0xFF && _countAtLeast(&t, GBA_GBABR_EVENT_NOR_ERASE, 1));

	/* FRAM bank 0 then bank 1 using the real SRAM aperture and ROM bank-select write. */
	t.core->busWrite8(t.core, SRAM_BASE + 0x1234, 0x5A);
	_w16(&t, 0x01000000, 1);
	t.core->busWrite8(t.core, SRAM_BASE + 0x1234, 0xA5);
	uint8_t* fram = GBAGBABRFRAM(t.cart);
	_result("Mini128 FRAM bank select/write", fram && fram[0x1234] == 0x5A && fram[0x10000 + 0x1234] == 0xA5 && _countAtLeast(&t, GBA_GBABR_EVENT_FRAM_WRITE, 2));

	/* Mini128 mapper/FRAM alias semantics from GBABR FIX201M/R13A:
	 * while CONFIG2 is unlocked, offsets 2..5 are mapper latches only and must
	 * not corrupt the aliased FRAM bytes. CONFIG2 bit 7 locks the mapper for
	 * this power cycle; after that, offsets 2..5 are ordinary FRAM again. */
	uint8_t aliasBefore[4] = { 0x12, 0x34, 0x56, 0x78 };
	for (unsigned i = 0; i < 4; ++i) fram[0x10000 + 2 + i] = aliasBefore[i];
	uint64_t framEventsBeforeMapper = GBAGBABRGetEventStats(t.cart)->counts[GBA_GBABR_EVENT_FRAM_WRITE];
	t.core->busWrite8(t.core, SRAM_BASE + 5, 0x00); /* CONFIG4 */
	t.core->busWrite8(t.core, SRAM_BASE + 2, 0x10); /* CONFIG1: bank 1 */
	t.core->busWrite8(t.core, SRAM_BASE + 3, 0x40); /* CONFIG2: unlocked, block 0 */
	t.core->busWrite8(t.core, SRAM_BASE + 4, 0x20); /* CONFIG3 */
	t.core->busWrite8(t.core, SRAM_BASE + 3, 0xC0); /* CONFIG2: final lock */
	bool aliasesPreserved = true;
	for (unsigned i = 0; i < 4; ++i) if (fram[0x10000 + 2 + i] != aliasBefore[i]) aliasesPreserved = false;
	_result("Mini128 unlocked mapper preserves FRAM aliases", aliasesPreserved &&
	        GBAGBABRGetEventStats(t.cart)->counts[GBA_GBABR_EVENT_FRAM_WRITE] == framEventsBeforeMapper);
	_result("Mini128 mapper latch/lock events", _countAtLeast(&t, GBA_GBABR_EVENT_MAPPER_CHANGE, 6));

	uint64_t mapperEventsAfterLock = GBAGBABRGetEventStats(t.cart)->counts[GBA_GBABR_EVENT_MAPPER_CHANGE];
	uint8_t aliasAfter[4] = { 0xA1, 0xB2, 0xC3, 0xD4 };
	for (unsigned i = 0; i < 4; ++i) t.core->busWrite8(t.core, SRAM_BASE + 2 + i, aliasAfter[i]);
	bool postLockFram = true;
	for (unsigned i = 0; i < 4; ++i) if (fram[0x10000 + 2 + i] != aliasAfter[i]) postLockFram = false;
	_result("Mini128 post-lock aliases are FRAM", postLockFram &&
	        GBAGBABRGetEventStats(t.cart)->counts[GBA_GBABR_EVENT_MAPPER_CHANGE] == mapperEventsAfterLock &&
	        GBAGBABRGetEventStats(t.cart)->counts[GBA_GBABR_EVENT_FRAM_WRITE] >= framEventsBeforeMapper + 4);
	_freeCore(&t);
}

static void _testStrictAndColdReset(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m6m", "0x80000:0x10000", NULL, true);
	_result("Strict/cold-reset fixture", ok);
	if (!ok) return;
	_m6Program(&t, 0x100000, 0x1111);
	_result("Strict out-of-range write rejected", _countAtLeast(&t, GBA_GBABR_EVENT_ILLEGAL_NOR_WRITE, 1));
	_m6Program(&t, 0x80000, 0xA55A);
	bool before = GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x5A;
	bool resetOk = GBAGBABRColdReset(t.gba, t.cart);
	t.core->reset(t.core);
	bool after = GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x5A;
	_result("Cold reset preserves NOR", resetOk && before && after && _countAtLeast(&t, GBA_GBABR_EVENT_COLD_RESET, 1));
	_freeCore(&t);
}

static void _testSaveScratch(void) {
	setenv("MGBA_GBABR_SAVE_SCRATCH_BYTES", "32768", 1);
	struct TestCore t;
	bool ok = _newCore(&t, "m36", "0x900000:0x40000", NULL, true);
	_result("M36 volatile save scratch fixture", ok);
	if (!ok) { unsetenv("MGBA_GBABR_SAVE_SCRATCH_BYTES"); return; }
	t.core->busWrite8(t.core, SRAM_BASE + 0x1234, 0xA5);
	uint8_t v = t.core->busRead8(t.core, SRAM_BASE + 0x1234);
	const struct GBAGBABREventStats* st = GBAGBABRGetEventStats(t.cart);
	_result("Save scratch read/write is not native SaveRAM", v == 0xA5 && st->counts[GBA_GBABR_EVENT_SAVE_SCRATCH_READ] >= 1 && st->counts[GBA_GBABR_EVENT_SAVE_SCRATCH_WRITE] >= 1 && st->counts[GBA_GBABR_EVENT_NATIVE_SAVE_ACCESS] == 0);
	t.core->busWrite8(t.core, SRAM_BASE + 0x9000, 0x5A);
	_result("Save scratch boundary remains strict", GBAGBABRGetEventStats(t.cart)->counts[GBA_GBABR_EVENT_NATIVE_SAVE_ACCESS] >= 1);
	struct VFile* state = VFileMemChunk(NULL, 0);
	bool saveOk = state && mCoreSaveStateNamed(t.core, state, SAVESTATE_SAVEDATA | SAVESTATE_RTC);
	t.core->busWrite8(t.core, SRAM_BASE + 0x1234, 0x11);
	if (state) state->seek(state, 0, SEEK_SET);
	bool loadOk = state && mCoreLoadStateNamed(t.core, state, 0);
	_result("Savestate rewinds volatile save scratch", saveOk && loadOk && t.core->busRead8(t.core, SRAM_BASE + 0x1234) == 0xA5);
	GBAGBABRColdReset(t.gba, t.cart);
	_result("Cold reset clears volatile save scratch", t.core->busRead8(t.core, SRAM_BASE + 0x1234) == 0x00);
	if (state) state->close(state);
	_freeCore(&t);
	unsetenv("MGBA_GBABR_SAVE_SCRATCH_BYTES");
}

static void _testInputMovie(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m6m", "0x80000:0x10000", NULL, true);
	_result("Input movie fixture", ok);
	if (!ok) return;
	const char* path = "/tmp/mgba-gbavc-selftest.movie";
	remove(path);
	bool startOk = GBAGBABRStartInputRecording(t.gba, t.cart, path);
	t.core->setKeys(t.core, 1u);
	t.core->setKeys(t.core, 0u);
	bool active = GBAGBABRInputRecordingActive(t.cart);
	GBAGBABRStopInputRecording(t.cart);
	FILE* f = fopen(path, "r");
	char buf[2048] = {0};
	if (f) { fread(buf, 1, sizeof(buf) - 1, f); fclose(f); }
	bool content = strstr(buf, "0x001") && strstr(buf, "0x000");
	_result("Input movie records key transitions", startOk && active && content && !GBAGBABRInputRecordingActive(t.cart));
	remove(path);
	_freeCore(&t);
}

static void _testStateKeepCart(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m6m", "0x80000:0x10000", NULL, true);
	_result("State-keep-cart fixture", ok);
	if (!ok) return;
	_m6Program(&t, 0x80000, 0xA55A);
	struct VFile* state = VFileMemChunk(NULL, 0);
	bool saveOk = state && mCoreSaveStateNamed(t.core, state, SAVESTATE_SAVEDATA | SAVESTATE_RTC);
	_m6Program(&t, 0x80000, 0xA050);
	bool changed = GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x50 && GBAGBABRPhysicalNOR(t.cart)[0x80001] == 0xA0;
	setenv("MGBA_GBABR_KEEP_CART_ON_STATE_LOAD", "1", 1);
	if (state) state->seek(state, 0, SEEK_SET);
	bool loadOk = state && mCoreLoadStateNamed(t.core, state, 0);
	unsetenv("MGBA_GBABR_KEEP_CART_ON_STATE_LOAD");
	bool kept = GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x50 && GBAGBABRPhysicalNOR(t.cart)[0x80001] == 0xA0;
	_result("Savestate can preserve current virtual NOR", saveOk && changed && loadOk && kept);
	if (state) state->close(state);
	_freeCore(&t);
}

static void _testSavestate(void) {
	struct TestCore t;
	bool ok = _newCore(&t, "m6m", "0x80000:0x10000", NULL, true);
	_result("Savestate fixture", ok);
	if (!ok) return;
	_m6Program(&t, 0x80000, 0xA55A);
	struct VFile* state = VFileMemChunk(NULL, 0);
	bool saveOk = state && mCoreSaveStateNamed(t.core, state, SAVESTATE_SAVEDATA | SAVESTATE_RTC);
	/* Additional valid 1->0 mutation changes A55A to A050. */
	_m6Program(&t, 0x80000, 0xA050);
	bool changed = GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x50 && GBAGBABRPhysicalNOR(t.cart)[0x80001] == 0xA0;
	if (state) state->seek(state, 0, SEEK_SET);
	bool loadOk = state && mCoreLoadStateNamed(t.core, state, 0);
	bool restored = GBAGBABRPhysicalNOR(t.cart)[0x80000] == 0x5A && GBAGBABRPhysicalNOR(t.cart)[0x80001] == 0xA5;
	_result("Savestate rewinds virtual NOR", saveOk && changed && loadOk && restored);
	if (state) state->close(state);
	_freeCore(&t);
}

static bool _writeFilledFile(const char* path, size_t bytes, uint32_t marker) {
	FILE* f = fopen(path, "wb");
	if (!f) return false;
	uint8_t page[4096]; memset(page, 0xFF, sizeof(page));
	page[0] = marker & 0xFF; page[1] = (marker >> 8) & 0xFF; page[2] = (marker >> 16) & 0xFF; page[3] = marker >> 24;
	for (size_t off = 0; off < bytes; off += sizeof(page)) {
		size_t n = bytes - off < sizeof(page) ? bytes - off : sizeof(page);
		if (fwrite(page, 1, n, f) != n) { fclose(f); return false; }
		memset(page, 0xFF, sizeof(page));
	}
	fclose(f); return true;
}

static void _testExistingMelonDSCart(void) {
	const char* m6 = "/tmp/mgba-gbavc-existing-m6m.nor";
	remove(m6);
	bool fileOk = _writeFilledFile(m6, 8u * 1024u * 1024u, 0xEA123456u);
	struct TestCore t;
	bool ok = fileOk && _newCoreConfigured(&t, "m6", NULL, NULL, false, m6, NULL, true);
	_result("Existing melonDS-style M6 backing activates", ok);
	if (ok) {
		uint8_t* nor = GBAGBABRPhysicalNOR(t.cart);
		_result("Existing backing is not overlaid by launcher ROM", nor && nor[0] == 0x56 && nor[1] == 0x34 && nor[2] == 0x12 && nor[3] == 0xEA);
		_result("Existing cart cold aperture becomes mGBA ROM", ((uint8_t*)t.gba->memory.rom)[0] == 0x56 && ((uint8_t*)t.gba->memory.rom)[3] == 0xEA);
		_freeCore(&t);
	}
	remove(m6);

	const char* sn = "/tmp/mgba-gbavc-existing-mini128.nor";
	const char* sf = "/tmp/mgba-gbavc-existing-mini128.fram";
	remove(sn); remove(sf);
	fileOk = _writeFilledFile(sn, 128u * 1024u * 1024u, 0xEA654321u) && _writeFilledFile(sf, 128u * 1024u, 0xFFFFFFFFu);
	ok = fileOk && _newCoreConfigured(&t, "mini128", NULL, NULL, false, sn, sf, true);
	_result("Existing melonDS Mini128 NOR+FRAM activate", ok);
	if (ok) {
		_result("Existing Mini128 cold-boots physical bank 0", GBAGBABRPhysicalNOR(t.cart)[0] == 0x21 && ((uint8_t*)t.gba->memory.rom)[0] == 0x21);
		_freeCore(&t);
	}
	remove(sn); remove(sf);
}

int main(void) {
	printf("mGBA protocol/integration selftest\n");
	printf("Writes use mCore bus functions, not private NOR handlers.\n\n");
	_testM6();
	_testM28();
	_testM36();
	_testD137();
	_testMX26();
	_testS29();
	_testStrictAndColdReset();
	_testSaveScratch();
	_testInputMovie();
	_testStateKeepCart();
	_testSavestate();
	_testExistingMelonDSCart();
	printf("\nSELFTEST RESULT: %s (%d failures)\n", gFailures ? "FAIL" : "PASS", gFailures);
	_clearEnv();
	return gFailures ? 1 : 0;
}
