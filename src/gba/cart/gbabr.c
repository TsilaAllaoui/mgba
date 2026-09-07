/* mGBA mGBA adapter for the reusable GBAVirtualCart core.
 *
 * NOR protocols, JEDEC/CFI, erase/program state machines, Mini128 mapper/FRAM
 * and persistence live in externals/GBAVirtualCart. This file intentionally
 * keeps only mGBA/analyzer concerns: visible ROM ownership, strict write
 * ranges, event statistics, volatile save scratch, movie recording and the
 * compact differential savestate payload used by AutoQA.
 */
#include <mgba/internal/gba/cart/gbabr.h>

#include <gbavirtualcart/gbavc.h>

#include <mgba/core/log.h>
#include <mgba/core/serialize.h>
#include <mgba/internal/arm/arm.h>
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/memory.h>
#include <mgba/internal/gba/savedata.h>
#include <mgba-util/memory.h>
#include <mgba-util/vfs.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define GBABR_MIB (1024u * 1024u)
#define GBABR_VISIBLE_BYTES (32u * GBABR_MIB)
#define GBABR_PAGE_BYTES 4096u
#define GBABR_MAX_RANGES 64u
#define GBABR_SAVE_SCRATCH_MAX (64u * 1024u)
#define GBABR_MAPPER_BLOCK_BYTES (512u * 1024u)
#define GBABR_STATE_MAGIC 0x56415347u /* GSAV */
#define GBABR_STATE_VERSION 4u

struct GBAGBABRRange {
	uint32_t offset;
	uint32_t bytes;
};

struct GBAGBABRCart {
	struct GBA* gba;
	gbavc_cart* core;
	const gbavc_profile_info* profile;
	char profileKey[64];
	char profileName[128];
	size_t capacity;
	uint8_t* visible;
	uint8_t* baseNor;
	uint8_t* baseFram;
	uint8_t* dirtyPages;
	size_t pageCount;

	uint8_t saveScratch[GBABR_SAVE_SCRATCH_MAX];
	uint32_t saveScratchBytes;
	bool hasFram;
	bool existingCart;
	uint32_t romOffset;

	bool strict;
	bool nativeSaveAllowed;
	struct GBAGBABRRange ranges[GBABR_MAX_RANGES];
	size_t rangeCount;

	char norPath[1024];
	char framPath[1024];
	char eventsPath[1024];
	bool tempNor;
	bool tempFram;

	struct GBAGBABREventStats stats;

	FILE* movieFile;
	uint32_t movieStartFrame;
	uint32_t movieLastKeys;
	bool movieHaveKeys;
	char moviePath[1024];
};

struct GBAGBABRStateHeader {
	uint32_t magic;
	uint32_t version;
	char profileKey[64];
	uint32_t pageCount;
	uint32_t framBytes;
	uint32_t scratchBytes;
	gbavc_runtime_state runtime;
};

static const char* _eventName(enum GBAGBABREventType type) {
	static const char* const names[] = {
		"CART_INIT", "NOR_PROGRAM", "NOR_ERASE", "FRAM_WRITE", "MAPPER_CHANGE",
		"SAVE_SCRATCH_READ", "SAVE_SCRATCH_WRITE", "ILLEGAL_NOR_WRITE", "INVALID_NOR_SEQUENCE", "NATIVE_SAVE_ACCESS", "COLD_RESET"
	};
	return type < GBA_GBABR_EVENT_MAX ? names[type] : "UNKNOWN";
}

static bool _envBool1(const char* primary, const char* legacy, bool fallback) {
	const char* value = primary ? getenv(primary) : NULL;
	if ((!value || !*value) && legacy) value = getenv(legacy);
	if (!value || !*value) return fallback;
	return strcmp(value, "0") != 0 && strcasecmp(value, "false") != 0 && strcasecmp(value, "no") != 0;
}

static uint32_t _envU321(const char* primary, const char* legacy, uint32_t fallback) {
	const char* value = primary ? getenv(primary) : NULL;
	if ((!value || !*value) && legacy) value = getenv(legacy);
	if (!value || !*value) return fallback;
	char* end = NULL;
	unsigned long parsed = strtoul(value, &end, 0);
	if (end == value || *end || parsed > UINT32_MAX) return fallback;
	return (uint32_t) parsed;
}

static const char* _envString1(const char* primary, const char* legacy) {
	const char* value = primary ? getenv(primary) : NULL;
	if ((!value || !*value) && legacy) value = getenv(legacy);
	return value && *value ? value : NULL;
}

static void _copyEnvPath1(char* out, size_t outSize, const char* primary, const char* legacy, const char* fallback) {
	const char* value = _envString1(primary, legacy);
	if (!value) value = fallback;
	if (!value) value = "";
	snprintf(out, outSize, "%s", value);
}

static bool _fileExact(const char* path, size_t bytes) {
	if (!path || !*path) return false;
	struct stat st;
	return stat(path, &st) == 0 && S_ISREG(st.st_mode) && (uint64_t) st.st_size == bytes;
}

static void _parseRanges(struct GBAGBABRCart* cart, const char* value) {
	cart->rangeCount = 0;
	if (!value || !*value) return;
	char* work = strdup(value);
	if (!work) return;
	char* save = NULL;
	for (char* tok = strtok_r(work, ",;", &save); tok && cart->rangeCount < GBABR_MAX_RANGES; tok = strtok_r(NULL, ",;", &save)) {
		char* colon = strchr(tok, ':');
		if (!colon) continue;
		*colon = '\0';
		char* end1 = NULL;
		char* end2 = NULL;
		unsigned long off = strtoul(tok, &end1, 0);
		unsigned long len = strtoul(colon + 1, &end2, 0);
		if (end1 == tok || *end1 || end2 == colon + 1 || *end2 || !len || off > UINT32_MAX || len > UINT32_MAX) continue;
		cart->ranges[cart->rangeCount].offset = (uint32_t) off;
		cart->ranges[cart->rangeCount].bytes = (uint32_t) len;
		++cart->rangeCount;
	}
	free(work);
}

static bool _overlap(uint32_t a, uint32_t alen, uint32_t b, uint32_t blen) {
	uint64_t ae = (uint64_t) a + alen;
	uint64_t be = (uint64_t) b + blen;
	return (uint64_t) a < be && (uint64_t) b < ae;
}

static bool _hardForbidden(const struct GBAGBABRCart* cart, uint32_t offset, uint32_t bytes) {
	if ((uint64_t) offset + bytes > cart->capacity) return true;
	if (!strcmp(cart->profile->key, "m28w640fs-t70za6")) {
		if (_overlap(offset, bytes, 0x000000, 0x010000)) return true;
		if (_overlap(offset, bytes, 0x040000, 0x020000)) return true;
	}
	if (!strcmp(cart->profile->key, "m6mgd137")) {
		if (_overlap(offset, bytes, 0x7F0000, 0x020000)) return true;
	}
	return false;
}

static bool _authorized(const struct GBAGBABRCart* cart, uint32_t offset, uint32_t bytes) {
	if (!bytes || _hardForbidden(cart, offset, bytes)) return false;
	if (!cart->strict) return true;
	uint64_t cursor = offset;
	uint64_t end = (uint64_t) offset + bytes;
	while (cursor < end) {
		uint64_t best = cursor;
		for (size_t i = 0; i < cart->rangeCount; ++i) {
			uint64_t rs = cart->ranges[i].offset;
			uint64_t re = rs + cart->ranges[i].bytes;
			if (rs <= cursor && re > best) best = re;
		}
		if (best == cursor) return false;
		cursor = best;
	}
	return true;
}

static void _markDirty(struct GBAGBABRCart* cart, uint32_t offset, uint32_t bytes) {
	if (!bytes || offset >= cart->capacity) return;
	uint64_t e = (uint64_t) offset + bytes;
	if (e > cart->capacity) e = cart->capacity;
	size_t first = offset / GBABR_PAGE_BYTES;
	size_t last = ((size_t) e - 1) / GBABR_PAGE_BYTES;
	for (size_t p = first; p <= last && p < cart->pageCount; ++p) cart->dirtyPages[p] = 1;
}

static void _emit(struct GBAGBABRCart* cart, enum GBAGBABREventType type,
                  uint32_t cpuAddress, uint32_t physical, uint32_t value, uint32_t bytes) {
	if (!cart || type >= GBA_GBABR_EVENT_MAX) return;
	++cart->stats.counts[type];
	cart->stats.lastType = type;
	cart->stats.lastFrame = cart->gba ? cart->gba->video.frameCounter : 0;
	cart->stats.lastPc = cart->gba && cart->gba->cpu ? cart->gba->cpu->gprs[ARM_PC] : 0;
	cart->stats.lastCpuAddress = cpuAddress;
	cart->stats.lastPhysicalAddress = physical;
	cart->stats.lastValue = value;
	if (cart->eventsPath[0]) {
		FILE* f = fopen(cart->eventsPath, "a");
		if (f) {
			fprintf(f, "{\"event\":\"%s\",\"frame\":%u,\"pc\":%u,\"cpu_address\":%u,\"physical\":%u,\"value\":%u,\"bytes\":%u}\n",
		        _eventName(type), cart->stats.lastFrame, cart->stats.lastPc, cpuAddress, physical, value, bytes);
			fclose(f);
		}
	}
}

static void _refreshVisible(struct GBAGBABRCart* cart) {
	if (!cart || !cart->visible || !cart->core) return;
	uint8_t* nor = gbavc_nor_data(cart->core);
	if (!nor) return;
	for (uint32_t local = 0; local < GBABR_VISIBLE_BYTES;) {
		uint32_t phys = gbavc_translate_rom_address(cart->core, local);
		size_t maxPhysical = cart->capacity - phys;
		size_t maxVisible = GBABR_VISIBLE_BYTES - local;
		size_t n = maxPhysical < maxVisible ? maxPhysical : maxVisible;
		if (!n) break;
		memcpy(cart->visible + local, nor + phys, n);
		local += (uint32_t) n;
	}
	if (cart->gba) {
		cart->gba->memory.rom = (uint32_t*) cart->visible;
		cart->gba->memory.romSize = GBABR_VISIBLE_BYTES;
		cart->gba->memory.romMask = GBABR_VISIBLE_BYTES - 1;
		cart->gba->memory.hw.gpioBase = &((uint16_t*) cart->gba->memory.rom)[GPIO_REG_DATA >> 1];
		if (cart->gba->cpu && cart->gba->memory.activeRegion >= GBA_REGION_ROM0 && cart->gba->memory.activeRegion <= GBA_REGION_ROM2_EX) {
			cart->gba->cpu->memory.setActiveRegion(cart->gba->cpu, cart->gba->cpu->gprs[ARM_PC]);
		}
	}
}

static void _syncVisiblePhysical(struct GBAGBABRCart* cart, uint32_t physical, uint32_t bytes) {
	if (!cart || !bytes || physical >= cart->capacity) return;
	uint8_t* nor = gbavc_nor_data(cart->core);
	if (!nor) return;
	if (!strcmp(cart->profile->protocol, "amd-buffered-s29")) {
		uint32_t view = gbavc_view_base(cart->core);
		uint32_t first = cart->capacity - view;
		if (first > GBABR_VISIBLE_BYTES) first = GBABR_VISIBLE_BYTES;
		uint64_t ps = physical, pe = (uint64_t) physical + bytes;
		uint64_t s1 = view, e1 = (uint64_t) view + first;
		if (ps < e1 && pe > s1) {
			uint64_t cs = ps > s1 ? ps : s1, ce = pe < e1 ? pe : e1;
			memcpy(cart->visible + (cs - s1), nor + cs, ce - cs);
		}
		if (first < GBABR_VISIBLE_BYTES) {
			uint64_t s2 = 0, e2 = GBABR_VISIBLE_BYTES - first;
			if (ps < e2 && pe > s2) {
				uint64_t cs = ps, ce = pe < e2 ? pe : e2;
				memcpy(cart->visible + first + cs, nor + cs, ce - cs);
			}
		}
		return;
	}
	for (uint64_t local = physical; local < GBABR_VISIBLE_BYTES; local += cart->capacity) {
		size_t n = bytes;
		if (local + n > GBABR_VISIBLE_BYTES) n = GBABR_VISIBLE_BYTES - local;
		if ((uint64_t) physical + n > cart->capacity) n = cart->capacity - physical;
		memcpy(cart->visible + local, nor + physical, n);
	}
}

static enum GBAGBABREventType _mapCoreEvent(const char* event) {
	if (!strcmp(event, "NOR_PROGRAM")) return GBA_GBABR_EVENT_NOR_PROGRAM;
	if (!strcmp(event, "NOR_ERASE")) return GBA_GBABR_EVENT_NOR_ERASE;
	if (!strcmp(event, "FRAM_WRITE")) return GBA_GBABR_EVENT_FRAM_WRITE;
	if (!strcmp(event, "MAPPER_CHANGE") || !strcmp(event, "FRAM_BANK")) return GBA_GBABR_EVENT_MAPPER_CHANGE;
	if (!strcmp(event, "INVALID_NOR_SEQUENCE")) return GBA_GBABR_EVENT_INVALID_NOR_SEQUENCE;
	if (!strcmp(event, "NOR_PROTECT_REJECT")) return GBA_GBABR_EVENT_ILLEGAL_NOR_WRITE;
	if (!strcmp(event, "COLD_RESET")) return GBA_GBABR_EVENT_COLD_RESET;
	return GBA_GBABR_EVENT_MAX;
}

static void _coreEvent(void* user, const char* event, uint32_t cpu, uint32_t physical, uint32_t value, uint32_t bytes) {
	struct GBAGBABRCart* cart = user;
	if (!cart) return;
	enum GBAGBABREventType type = _mapCoreEvent(event);
	if (type == GBA_GBABR_EVENT_MAX) return;
	if (type == GBA_GBABR_EVENT_NOR_PROGRAM || type == GBA_GBABR_EVENT_NOR_ERASE) {
		_markDirty(cart, physical, bytes);
		_syncVisiblePhysical(cart, physical, bytes);
	} else if (type == GBA_GBABR_EVENT_MAPPER_CHANGE || type == GBA_GBABR_EVENT_COLD_RESET) {
		_refreshVisible(cart);
	}
	_emit(cart, type, cpu, physical, value, bytes);
}

static int _writeFilter(void* user, uint32_t physical, uint32_t bytes, uint32_t value, int isErase) {
	UNUSED(value);
	UNUSED(isErase);
	struct GBAGBABRCart* cart = user;
	return cart && _authorized(cart, physical, bytes);
}

static void _setAnalyzerInitialView(struct GBAGBABRCart* cart) {
	if (!cart || cart->existingCart || strcmp(cart->profile->key, "s29") || !cart->romOffset) return;
	gbavc_runtime_state st = {0};
	st.view_base = cart->romOffset;
	uint32_t blocks = cart->romOffset / GBABR_MAPPER_BLOCK_BYTES;
	st.mapper_cfg1 = (blocks / 64u) << 4;
	st.mapper_cfg2 = 0x40u + (blocks % 64u);
	gbavc_runtime_state_set(cart->core, &st);
}

static bool _prepareVisibleOwnership(struct GBA* gba, struct GBAGBABRCart* cart, bool norExisted) {
	uint8_t* old = (uint8_t*) gba->memory.rom;
	size_t sourceBytes = gba->pristineRomSize;
	if (sourceBytes > GBABR_VISIBLE_BYTES) sourceBytes = GBABR_VISIBLE_BYTES;
	uint8_t* source = malloc(sourceBytes ? sourceBytes : 1);
	if (!source) return false;
	if (sourceBytes) memcpy(source, old, sourceBytes);

#ifndef FIXED_ROM_BUFFER
	if (gba->isPristine) gba->romVf->unmap(gba->romVf, gba->memory.rom, gba->pristineRomSize);
	else mappedMemoryFree(gba->memory.rom, GBA_SIZE_ROM0);
#endif
	gba->isPristine = false;

	if (!norExisted && !cart->existingCart) {
		if ((uint64_t) cart->romOffset + sourceBytes > cart->capacity) {
			free(source);
			return false;
		}
		if (sourceBytes) memcpy(gbavc_nor_data(cart->core) + cart->romOffset, source, sourceBytes);
		if (!gbavc_persist_all(cart->core)) {
			free(source);
			return false;
		}
	}
	free(source);
	_setAnalyzerInitialView(cart);
	_refreshVisible(cart);
	return true;
}

bool GBAGBABRTryActivate(struct GBA* gba, struct GBAGBABRCart** outCart) {
	if (outCart) *outCart = NULL;
	const char* requested = _envString1("MGBA_GBAVC_PROFILE", "MGBA_GBABR_PROFILE");
	if (!requested) return false;
	const gbavc_profile_info* profile = gbavc_profile_find(requested);
	if (!profile) {
		mLOG(GBA_MEM, ERROR, "Unknown GBAVirtualCart profile: %s", requested);
		return false;
	}

	struct GBAGBABRCart* cart = calloc(1, sizeof(*cart));
	if (!cart) return false;
	cart->gba = gba;
	cart->profile = profile;
	cart->capacity = profile->capacity_bytes;
	cart->hasFram = profile->fram_bytes != 0;
	snprintf(cart->profileKey, sizeof(cart->profileKey), "%s", profile->key);
	snprintf(cart->profileName, sizeof(cart->profileName), "%s", profile->display_name);
	cart->strict = _envBool1("MGBA_GBAVC_STRICT", "MGBA_GBABR_STRICT", true);
	cart->existingCart = _envBool1("MGBA_GBAVC_EXISTING", NULL, false);
	cart->nativeSaveAllowed = _envBool1("MGBA_GBAVC_NATIVE_SAVE_ALLOWED", "MGBA_GBABR_NATIVE_SAVE_ALLOWED", cart->hasFram);
	cart->romOffset = _envU321("MGBA_GBAVC_ROM_OFFSET", "MGBA_GBABR_ROM_OFFSET", 0);
	cart->saveScratchBytes = _envU321("MGBA_GBAVC_SAVE_SCRATCH_BYTES", "MGBA_GBABR_SAVE_SCRATCH_BYTES", 0);
	if (cart->saveScratchBytes > GBABR_SAVE_SCRATCH_MAX) cart->saveScratchBytes = GBABR_SAVE_SCRATCH_MAX;
	_parseRanges(cart, _envString1("MGBA_GBAVC_ALLOW", "MGBA_GBABR_ALLOW"));
	const char* norEnv = _envString1("MGBA_GBAVC_NOR", "MGBA_GBABR_NOR");
	const char* framEnv = _envString1("MGBA_GBAVC_FRAM", "MGBA_GBABR_FRAM");
	if (norEnv) snprintf(cart->norPath, sizeof(cart->norPath), "%s", norEnv);
	else { static unsigned tempSeq; snprintf(cart->norPath, sizeof(cart->norPath), "/tmp/mgba-gbavc-%ld-%u-%s.nor", (long)getpid(), ++tempSeq, profile->key); cart->tempNor = true; remove(cart->norPath); }
	if (profile->fram_bytes) {
		if (framEnv) snprintf(cart->framPath, sizeof(cart->framPath), "%s", framEnv);
		else { static unsigned framSeq; snprintf(cart->framPath, sizeof(cart->framPath), "/tmp/mgba-gbavc-%ld-%u-%s.fram", (long)getpid(), ++framSeq, profile->key); cart->tempFram = true; remove(cart->framPath); }
	}
	_copyEnvPath1(cart->eventsPath, sizeof(cart->eventsPath), "MGBA_GBAVC_EVENTS", "MGBA_GBABR_EVENTS", "");
	bool norExisted = _fileExact(cart->norPath, cart->capacity);
	if (cart->existingCart && !norExisted) {
		mLOG(GBA_MEM, ERROR, "Existing GBAVirtualCart NOR not found or wrong size: %s", cart->norPath);
		free(cart);
		return false;
	}
	if (cart->hasFram && cart->existingCart && !_fileExact(cart->framPath, profile->fram_bytes)) {
		mLOG(GBA_MEM, ERROR, "Existing GBAVirtualCart FRAM not found or wrong size: %s", cart->framPath);
		free(cart);
		return false;
	}
	gbavc_config cfg = { profile->key, cart->norPath, cart->framPath, "", _envBool1("MGBA_GBAVC_RESET", NULL, false) ? 1 : 0 };
	cart->core = gbavc_create(&cfg);
	if (!cart->core) { free(cart); return false; }
	gbavc_set_event_callback(cart->core, _coreEvent, cart);
	gbavc_set_write_filter(cart->core, _writeFilter, cart);

	cart->visible = malloc(GBABR_VISIBLE_BYTES);
	cart->baseNor = malloc(cart->capacity);
	cart->pageCount = (cart->capacity + GBABR_PAGE_BYTES - 1) / GBABR_PAGE_BYTES;
	cart->dirtyPages = calloc(cart->pageCount, 1);
	if (cart->hasFram) cart->baseFram = malloc(profile->fram_bytes);
	if (!cart->visible || !cart->baseNor || !cart->dirtyPages || (cart->hasFram && !cart->baseFram)) {
		gbavc_destroy(cart->core); free(cart->baseFram); free(cart->dirtyPages); free(cart->baseNor); free(cart->visible); free(cart); return false;
	}

	if (!_prepareVisibleOwnership(gba, cart, norExisted)) {
		gbavc_destroy(cart->core); free(cart->baseFram); free(cart->dirtyPages); free(cart->baseNor); free(cart->visible); free(cart); return false;
	}
	memcpy(cart->baseNor, gbavc_nor_data(cart->core), cart->capacity);
	if (cart->hasFram) memcpy(cart->baseFram, gbavc_fram_data(cart->core), profile->fram_bytes);

	GBASavedataInitSRAM(&gba->memory.savedata);
	_emit(cart, GBA_GBABR_EVENT_CART_INIT, 0, gbavc_view_base(cart->core), (uint32_t) cart->capacity, 0);
	mLOG(GBA_MEM, INFO, "GBAVirtualCart active: %s key=%s capacity=%zu existing=%d strict=%d ranges=%zu",
	     cart->profileName, cart->profileKey, cart->capacity, cart->existingCart, cart->strict, cart->rangeCount);
	if (outCart) *outCart = cart;
	return true;
}

void GBAGBABRResetVolatile(struct GBA* gba, struct GBAGBABRCart* cart, bool coldReset) {
	UNUSED(gba);
	if (!cart) return;
	gbavc_reset(cart->core);
	if (!cart->existingCart) _setAnalyzerInitialView(cart);
	if (coldReset && cart->saveScratchBytes) memset(cart->saveScratch, 0, cart->saveScratchBytes);
	_refreshVisible(cart);
	/* gbavc_reset emits COLD_RESET through the callback. */
}

bool GBAGBABRColdReset(struct GBA* gba, struct GBAGBABRCart* cart) {
	if (!cart) return false;
	if (!GBAGBABRFlush(cart)) return false;
	GBAGBABRResetVolatile(gba, cart, true);
	return true;
}

void GBAGBABRDestroy(struct GBA* gba, struct GBAGBABRCart* cart) {
	if (!cart) return;
	GBAGBABRStopInputRecording(cart);
	GBAGBABRFlush(cart);
	if (gba && gba->memory.rom == (uint32_t*) cart->visible) gba->memory.rom = NULL;
	gbavc_destroy(cart->core);
	if (cart->tempNor) remove(cart->norPath);
	if (cart->tempFram) remove(cart->framPath);
	free(cart->baseFram);
	free(cart->dirtyPages);
	free(cart->baseNor);
	free(cart->visible);
	free(cart);
}

bool GBAGBABRReadROM16(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint16_t* value) {
	UNUSED(gba);
	if (!cart || !value) return false;
	*value = gbavc_read_rom16(cart->core, address);
	return true;
}

bool GBAGBABRReadROM32(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint32_t* value) {
	uint16_t lo, hi;
	if (!GBAGBABRReadROM16(gba, cart, address, &lo)) return false;
	if (!GBAGBABRReadROM16(gba, cart, address + 2, &hi)) return false;
	*value = (uint32_t) lo | ((uint32_t) hi << 16);
	return true;
}

void GBAGBABRWriteROM16(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint16_t value) {
	UNUSED(gba);
	if (cart) gbavc_write_rom16(cart->core, address, value);
}

uint8_t GBAGBABRReadSRAM8(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address) {
	if (!cart) return 0xFF;
	uint32_t off = address & 0xFFFFu;
	if (cart->hasFram) return gbavc_read_sram8(cart->core, address);
	if (off < cart->saveScratchBytes) {
		uint8_t value = cart->saveScratch[off];
		_emit(cart, GBA_GBABR_EVENT_SAVE_SCRATCH_READ, GBA_BASE_SRAM + off, off, value, 1);
		return value;
	}
	GBAGBABRNoteNativeSaveAccess(gba, cart, GBA_BASE_SRAM + off, 0, false);
	return 0xFF;
}

void GBAGBABRWriteSRAM8(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint8_t value) {
	if (!cart) return;
	uint32_t off = address & 0xFFFFu;
	if (cart->hasFram) { gbavc_write_sram8(cart->core, address, value); return; }
	if (off < cart->saveScratchBytes) {
		cart->saveScratch[off] = value;
		_emit(cart, GBA_GBABR_EVENT_SAVE_SCRATCH_WRITE, GBA_BASE_SRAM + off, off, value, 1);
		return;
	}
	GBAGBABRNoteNativeSaveAccess(gba, cart, GBA_BASE_SRAM + off, value, true);
}

void GBAGBABRNoteNativeSaveAccess(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint32_t value, bool write) {
	UNUSED(gba);
	if (!cart || cart->nativeSaveAllowed) return;
	_emit(cart, GBA_GBABR_EVENT_NATIVE_SAVE_ACCESS, address, 0, value, write ? 1 : 0);
}

bool GBAGBABRNativeSaveAllowed(const struct GBAGBABRCart* cart) { return cart && cart->nativeSaveAllowed; }
bool GBAGBABRFlush(struct GBAGBABRCart* cart) { return !cart || gbavc_flush(cart->core); }
const struct GBAGBABREventStats* GBAGBABRGetEventStats(const struct GBAGBABRCart* cart) { return cart ? &cart->stats : NULL; }
const char* GBAGBABRProfileName(const struct GBAGBABRCart* cart) { return cart ? cart->profileName : "none"; }
size_t GBAGBABRCapacity(const struct GBAGBABRCart* cart) { return cart ? cart->capacity : 0; }
uint8_t* GBAGBABRPhysicalNOR(struct GBAGBABRCart* cart) { return cart ? gbavc_nor_data(cart->core) : NULL; }
uint8_t* GBAGBABRFRAM(struct GBAGBABRCart* cart) { return cart && cart->hasFram ? gbavc_fram_data(cart->core) : NULL; }

bool GBAGBABRStartInputRecording(struct GBA* gba, struct GBAGBABRCart* cart, const char* path) {
	if (!cart || !path || !*path) return false;
	GBAGBABRStopInputRecording(cart);
	cart->movieFile = fopen(path, "w");
	if (!cart->movieFile) return false;
	strncpy(cart->moviePath, path, sizeof(cart->moviePath) - 1);
	cart->moviePath[sizeof(cart->moviePath) - 1] = '\0';
	cart->movieStartFrame = gba ? gba->video.frameCounter : 0;
	cart->movieLastKeys = gba ? gba->keysActive & 0x3FFu : 0;
	cart->movieHaveKeys = true;
	fprintf(cart->movieFile, "# mGBA deterministic input movie\n# relative_frame key_mask\n0 0x%03X\n", cart->movieLastKeys);
	fflush(cart->movieFile);
	return true;
}

void GBAGBABRStopInputRecording(struct GBAGBABRCart* cart) {
	if (!cart) return;
	if (cart->movieFile) { fflush(cart->movieFile); fclose(cart->movieFile); cart->movieFile = NULL; }
	cart->movieHaveKeys = false;
}

bool GBAGBABRInputRecordingActive(const struct GBAGBABRCart* cart) { return cart && cart->movieFile; }

void GBAGBABRRecordKeys(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t keys) {
	if (!gba || !cart || !cart->movieFile) return;
	keys &= 0x3FFu;
	if (cart->movieHaveKeys && keys == cart->movieLastKeys) return;
	uint32_t frame = gba->video.frameCounter - cart->movieStartFrame;
	fprintf(cart->movieFile, "%u 0x%03X\n", frame, keys);
	fflush(cart->movieFile);
	cart->movieLastKeys = keys;
	cart->movieHaveKeys = true;
}

bool GBAGBABRSaveExtraState(const struct GBAGBABRCart* cart, struct mStateExtdataItem* item) {
	if (!cart || !item) return false;
	uint32_t dirtyCount = 0;
	for (size_t p = 0; p < cart->pageCount; ++p) if (cart->dirtyPages[p]) ++dirtyCount;
	uint32_t framBytes = (uint32_t) gbavc_fram_size(cart->core);
	size_t size = sizeof(struct GBAGBABRStateHeader) + framBytes + cart->saveScratchBytes +
	              (size_t) dirtyCount * (sizeof(uint32_t) + GBABR_PAGE_BYTES);
	uint8_t* out = malloc(size);
	if (!out) return false;
	struct GBAGBABRStateHeader header = {0};
	header.magic = GBABR_STATE_MAGIC;
	header.version = GBABR_STATE_VERSION;
	snprintf(header.profileKey, sizeof(header.profileKey), "%s", cart->profileKey);
	header.pageCount = dirtyCount;
	header.framBytes = framBytes;
	header.scratchBytes = cart->saveScratchBytes;
	gbavc_runtime_state_get(cart->core, &header.runtime);
	memcpy(out, &header, sizeof(header));
	size_t cursor = sizeof(header);
	if (framBytes) { memcpy(out + cursor, gbavc_fram_data(cart->core), framBytes); cursor += framBytes; }
	if (cart->saveScratchBytes) { memcpy(out + cursor, cart->saveScratch, cart->saveScratchBytes); cursor += cart->saveScratchBytes; }
	uint8_t* nor = gbavc_nor_data(cart->core);
	for (size_t p = 0; p < cart->pageCount; ++p) {
		if (!cart->dirtyPages[p]) continue;
		uint32_t page = (uint32_t) p;
		memcpy(out + cursor, &page, sizeof(page)); cursor += sizeof(page);
		size_t offset = p * GBABR_PAGE_BYTES, bytes = GBABR_PAGE_BYTES;
		if (offset + bytes > cart->capacity) bytes = cart->capacity - offset;
		memcpy(out + cursor, nor + offset, bytes);
		if (bytes < GBABR_PAGE_BYTES) memset(out + cursor + bytes, 0xFF, GBABR_PAGE_BYTES - bytes);
		cursor += GBABR_PAGE_BYTES;
	}
	item->size = (int32_t) size; item->data = out; item->clean = free;
	return true;
}

bool GBAGBABRLoadExtraState(struct GBA* gba, struct GBAGBABRCart* cart, const struct mStateExtdataItem* item) {
	if (!cart || !item || !item->data || item->size < (int32_t) sizeof(struct GBAGBABRStateHeader)) return false;
	const uint8_t* in = item->data;
	struct GBAGBABRStateHeader header; memcpy(&header, in, sizeof(header));
	if (header.magic != GBABR_STATE_MAGIC || header.version != GBABR_STATE_VERSION || strcmp(header.profileKey, cart->profileKey)) return false;
	uint32_t currentFram = (uint32_t) gbavc_fram_size(cart->core);
	size_t expected = sizeof(header) + header.framBytes + header.scratchBytes + (size_t) header.pageCount * (sizeof(uint32_t) + GBABR_PAGE_BYTES);
	if (expected > (size_t) item->size || header.framBytes != currentFram || header.scratchBytes > GBABR_SAVE_SCRATCH_MAX) return false;
	uint8_t* nor = gbavc_nor_data(cart->core);
	for (size_t p = 0; p < cart->pageCount; ++p) {
		if (!cart->dirtyPages[p]) continue;
		size_t offset = p * GBABR_PAGE_BYTES, bytes = GBABR_PAGE_BYTES;
		if (offset + bytes > cart->capacity) bytes = cart->capacity - offset;
		memcpy(nor + offset, cart->baseNor + offset, bytes);
	}
	memset(cart->dirtyPages, 0, cart->pageCount);
	size_t cursor = sizeof(header);
	if (header.framBytes) { memcpy(gbavc_fram_data(cart->core), in + cursor, header.framBytes); cursor += header.framBytes; }
	cart->saveScratchBytes = header.scratchBytes;
	memset(cart->saveScratch, 0, sizeof(cart->saveScratch));
	if (header.scratchBytes) { memcpy(cart->saveScratch, in + cursor, header.scratchBytes); cursor += header.scratchBytes; }
	for (uint32_t i = 0; i < header.pageCount; ++i) {
		uint32_t page; memcpy(&page, in + cursor, sizeof(page)); cursor += sizeof(page);
		if (page >= cart->pageCount) return false;
		size_t offset = (size_t) page * GBABR_PAGE_BYTES, bytes = GBABR_PAGE_BYTES;
		if (offset + bytes > cart->capacity) bytes = cart->capacity - offset;
		memcpy(nor + offset, in + cursor, bytes); cursor += GBABR_PAGE_BYTES; cart->dirtyPages[page] = 1;
	}
	gbavc_runtime_state_set(cart->core, &header.runtime);
	gbavc_persist_all(cart->core);
	_refreshVisible(cart);
	UNUSED(gba);
	return true;
}
