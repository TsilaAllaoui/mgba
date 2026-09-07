/* mGBA adapter for the shared GBAVirtualCart core.
 *
 * This file is part of the mGBA-GBABR testing fork. It intentionally models
 * command traffic at the GBA Game Pak bus instead of hooking game save APIs.
 */
#ifndef GBA_GBABR_CART_H
#define GBA_GBABR_CART_H

#include <mgba-util/common.h>

CXX_GUARD_START

struct GBA;
struct mStateExtdataItem;

enum GBAGBABRProfile {
	GBA_GBABR_PROFILE_NONE = 0,
	GBA_GBABR_PROFILE_M6M,
	GBA_GBABR_PROFILE_M28,
	GBA_GBABR_PROFILE_M36,
	GBA_GBABR_PROFILE_M6MGD137,
	GBA_GBABR_PROFILE_MX26,
	GBA_GBABR_PROFILE_S29,
};

enum GBAGBABREventType {
	GBA_GBABR_EVENT_CART_INIT = 0,
	GBA_GBABR_EVENT_NOR_PROGRAM,
	GBA_GBABR_EVENT_NOR_ERASE,
	GBA_GBABR_EVENT_FRAM_WRITE,
	GBA_GBABR_EVENT_MAPPER_CHANGE,
	GBA_GBABR_EVENT_SAVE_SCRATCH_READ,
	GBA_GBABR_EVENT_SAVE_SCRATCH_WRITE,
	GBA_GBABR_EVENT_ILLEGAL_NOR_WRITE,
	GBA_GBABR_EVENT_INVALID_NOR_SEQUENCE,
	GBA_GBABR_EVENT_NATIVE_SAVE_ACCESS,
	GBA_GBABR_EVENT_COLD_RESET,
	GBA_GBABR_EVENT_MAX,
};

struct GBAGBABREventStats {
	uint64_t counts[GBA_GBABR_EVENT_MAX];
	uint32_t lastFrame;
	uint32_t lastPc;
	uint32_t lastCpuAddress;
	uint32_t lastPhysicalAddress;
	uint32_t lastValue;
	enum GBAGBABREventType lastType;
};

struct GBAGBABRCart;

/* Returns true and takes over the visible ROM mapping when MGBA_GBAVC_PROFILE
 * (or legacy MGBA_GBABR_PROFILE) requests a shared GBAVirtualCart profile. */
bool GBAGBABRTryActivate(struct GBA* gba, struct GBAGBABRCart** outCart);
void GBAGBABRDestroy(struct GBA* gba, struct GBAGBABRCart* cart);
void GBAGBABRResetVolatile(struct GBA* gba, struct GBAGBABRCart* cart, bool coldReset);

bool GBAGBABRReadROM16(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint16_t* value);
bool GBAGBABRReadROM32(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint32_t* value);
void GBAGBABRWriteROM16(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint16_t value);

uint8_t GBAGBABRReadSRAM8(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address);
void GBAGBABRWriteSRAM8(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint8_t value);
void GBAGBABRNoteNativeSaveAccess(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t address, uint32_t value, bool write);
bool GBAGBABRNativeSaveAllowed(const struct GBAGBABRCart* cart);

bool GBAGBABRFlush(struct GBAGBABRCart* cart);
bool GBAGBABRColdReset(struct GBA* gba, struct GBAGBABRCart* cart);
const struct GBAGBABREventStats* GBAGBABRGetEventStats(const struct GBAGBABRCart* cart);
const char* GBAGBABRProfileName(const struct GBAGBABRCart* cart);
size_t GBAGBABRCapacity(const struct GBAGBABRCart* cart);
uint8_t* GBAGBABRPhysicalNOR(struct GBAGBABRCart* cart);
uint8_t* GBAGBABRFRAM(struct GBAGBABRCart* cart);

/* Deterministic input movie recording used by the mGBA Qt frontend.
 * The file format is the same simple "relative_frame key_mask" format consumed
 * by mgba-gbabr-autoqa --movie. */
bool GBAGBABRStartInputRecording(struct GBA* gba, struct GBAGBABRCart* cart, const char* path);
void GBAGBABRStopInputRecording(struct GBAGBABRCart* cart);
bool GBAGBABRInputRecordingActive(const struct GBAGBABRCart* cart);
void GBAGBABRRecordKeys(struct GBA* gba, struct GBAGBABRCart* cart, uint32_t keys);

/* Extra-state payload used by ordinary mGBA savestates. */
bool GBAGBABRSaveExtraState(const struct GBAGBABRCart* cart, struct mStateExtdataItem* item);
bool GBAGBABRLoadExtraState(struct GBA* gba, struct GBAGBABRCart* cart, const struct mStateExtdataItem* item);

CXX_GUARD_END

#endif
