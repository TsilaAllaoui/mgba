/* Simple deterministic runner for the mGBA virtual cartridge. */
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/core/interface.h>
#include <mgba/core/serialize.h>
#include <mgba/internal/gba/cart/gbabr.h>
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/input.h>
#include <mgba-util/vfs.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct MovieEntry {
	uint32_t frame;
	uint32_t keys;
};

struct Options {
	const char* rom;
	const char* report;
	const char* state;
	const char* movie;
	const char* preEventState;
	const char* eventState;
	const char* finalState;
	uint32_t frames;
	uint32_t afterEvent;
	uint32_t checkpointInterval;
	uint32_t postReset;
	const char* require;
	const char* stopOn;
	bool coldResetOnEvent;
	bool stateKeepCart;
};

static bool gCrashed;
static bool gShutdown;

static void _crashed(void* context) { (void) context; gCrashed = true; }
static void _shutdown(void* context) { (void) context; gShutdown = true; }

static void _usage(const char* argv0) {
	fprintf(stderr,
	        "mGBA AutoQA\n"
	        "Usage: %s ROM.gba [options]\n\n"
	        "Options:\n"
	        "  --frames N                 Maximum frames (default 18000)\n"
	        "  --require TYPE             program|erase|fram|save|any\n"
	        "  --stop-on TYPE             Stop after event (same types)\n"
	        "  --after-event N            Frames to run after stop event (default 60)\n"
	        "  --cold-reset-on-event      Cold-reset only after the save/event stream settles\n"
	        "  --post-reset N             Frames to run after cold reset (default 600)\n"
	        "  --state FILE               Load an mGBA savestate before running\n"
	        "  --state-keep-cart          Load CPU/RAM state but keep current virtual NOR/FRAM\n"
	        "  --movie FILE               Replay simple input movie: 'frame keys'\n"
	        "  --pre-event-state FILE     Keep the latest rolling checkpoint and save it when stop event occurs\n"
	        "  --checkpoint-interval N    Rolling checkpoint interval in frames (default 60)\n"
	        "  --event-state FILE         Save state immediately after the first stop event\n"
	        "  --final-state FILE         Save state when the run finishes\n"
	        "  --report FILE              Write JSON report\n",
	        argv0);
}

static bool _parseU32(const char* s, uint32_t* out) {
	char* end = NULL;
	unsigned long v = strtoul(s, &end, 0);
	if (end == s || *end || v > UINT32_MAX) return false;
	*out = (uint32_t) v;
	return true;
}

static bool _parseOptions(int argc, char** argv, struct Options* o) {
	memset(o, 0, sizeof(*o));
	o->frames = 18000;
	o->afterEvent = 60;
	o->checkpointInterval = 60;
	o->postReset = 600;
	for (int i = 1; i < argc; ++i) {
		const char* a = argv[i];
		if (!strcmp(a, "--frames") && i + 1 < argc) {
			if (!_parseU32(argv[++i], &o->frames)) return false;
		} else if (!strcmp(a, "--after-event") && i + 1 < argc) {
			if (!_parseU32(argv[++i], &o->afterEvent)) return false;
		} else if (!strcmp(a, "--require") && i + 1 < argc) {
			o->require = argv[++i];
		} else if (!strcmp(a, "--stop-on") && i + 1 < argc) {
			o->stopOn = argv[++i];
		} else if (!strcmp(a, "--cold-reset-on-event")) {
			o->coldResetOnEvent = true;
		} else if (!strcmp(a, "--post-reset") && i + 1 < argc) {
			if (!_parseU32(argv[++i], &o->postReset)) return false;
		} else if (!strcmp(a, "--state") && i + 1 < argc) {
			o->state = argv[++i];
		} else if (!strcmp(a, "--state-keep-cart")) {
			o->stateKeepCart = true;
		} else if (!strcmp(a, "--movie") && i + 1 < argc) {
			o->movie = argv[++i];
		} else if (!strcmp(a, "--pre-event-state") && i + 1 < argc) {
			o->preEventState = argv[++i];
		} else if (!strcmp(a, "--checkpoint-interval") && i + 1 < argc) {
			if (!_parseU32(argv[++i], &o->checkpointInterval) || !o->checkpointInterval) return false;
		} else if (!strcmp(a, "--event-state") && i + 1 < argc) {
			o->eventState = argv[++i];
		} else if (!strcmp(a, "--final-state") && i + 1 < argc) {
			o->finalState = argv[++i];
		} else if (!strcmp(a, "--report") && i + 1 < argc) {
			o->report = argv[++i];
		} else if (a[0] == '-') {
			return false;
		} else if (!o->rom) {
			o->rom = a;
		} else {
			return false;
		}
	}
	return o->rom != NULL;
}

static uint32_t _keyMaskFromToken(const char* token) {
	if (!strcasecmp(token, "A")) return 1u << GBA_KEY_A;
	if (!strcasecmp(token, "B")) return 1u << GBA_KEY_B;
	if (!strcasecmp(token, "SELECT")) return 1u << GBA_KEY_SELECT;
	if (!strcasecmp(token, "START")) return 1u << GBA_KEY_START;
	if (!strcasecmp(token, "RIGHT")) return 1u << GBA_KEY_RIGHT;
	if (!strcasecmp(token, "LEFT")) return 1u << GBA_KEY_LEFT;
	if (!strcasecmp(token, "UP")) return 1u << GBA_KEY_UP;
	if (!strcasecmp(token, "DOWN")) return 1u << GBA_KEY_DOWN;
	if (!strcasecmp(token, "R")) return 1u << GBA_KEY_R;
	if (!strcasecmp(token, "L")) return 1u << GBA_KEY_L;
	if (!strcasecmp(token, "NONE")) return 0;
	return UINT32_MAX;
}

static bool _parseKeys(char* text, uint32_t* keys) {
	char* end = NULL;
	unsigned long numeric = strtoul(text, &end, 0);
	if (end != text && !*end && numeric <= 0x3FF) {
		*keys = (uint32_t) numeric;
		return true;
	}
	uint32_t out = 0;
	char* save = NULL;
	for (char* tok = strtok_r(text, "+|,", &save); tok; tok = strtok_r(NULL, "+|,", &save)) {
		uint32_t bit = _keyMaskFromToken(tok);
		if (bit == UINT32_MAX) return false;
		out |= bit;
	}
	*keys = out;
	return true;
}

static struct MovieEntry* _loadMovie(const char* path, size_t* count) {
	*count = 0;
	if (!path) return NULL;
	FILE* f = fopen(path, "r");
	if (!f) return NULL;
	size_t cap = 32;
	struct MovieEntry* entries = malloc(cap * sizeof(*entries));
	if (!entries) { fclose(f); return NULL; }
	char line[512];
	while (fgets(line, sizeof(line), f)) {
		char* p = line;
		while (*p == ' ' || *p == '\t') ++p;
		if (!*p || *p == '#' || *p == '\n') continue;
		char* keysText = p;
		while (*keysText && *keysText != ' ' && *keysText != '\t') ++keysText;
		if (!*keysText) { free(entries); fclose(f); return NULL; }
		*keysText++ = '\0';
		while (*keysText == ' ' || *keysText == '\t') ++keysText;
		char* nl = strpbrk(keysText, "\r\n");
		if (nl) *nl = '\0';
		uint32_t frame, keys;
		if (!_parseU32(p, &frame) || !_parseKeys(keysText, &keys)) { free(entries); fclose(f); return NULL; }
		if (*count == cap) {
			cap *= 2;
			struct MovieEntry* grown = realloc(entries, cap * sizeof(*entries));
			if (!grown) { free(entries); fclose(f); return NULL; }
			entries = grown;
		}
		entries[*count].frame = frame;
		entries[*count].keys = keys;
		++*count;
	}
	fclose(f);
	return entries;
}


static bool _saveStateFile(struct mCore* core, const char* path) {
	if (!path || !*path) return true;
	struct VFile* vf = VFileOpen(path, O_CREAT | O_TRUNC | O_RDWR);
	if (!vf) return false;
	bool ok = mCoreSaveStateNamed(core, vf, SAVESTATE_SAVEDATA | SAVESTATE_RTC);
	vf->close(vf);
	return ok;
}

static struct VFile* _captureState(struct mCore* core) {
	struct VFile* vf = VFileMemChunk(NULL, 0);
	if (!vf) return NULL;
	if (!mCoreSaveStateNamed(core, vf, SAVESTATE_SAVEDATA | SAVESTATE_RTC)) {
		vf->close(vf);
		return NULL;
	}
	vf->seek(vf, 0, SEEK_SET);
	return vf;
}

static bool _copyVFileToPath(struct VFile* src, const char* path) {
	if (!src || !path || !*path) return false;
	ssize_t size = src->size(src);
	if (size < 0) return false;
	uint8_t* data = malloc((size_t) size);
	if (!data && size) return false;
	src->seek(src, 0, SEEK_SET);
	bool ok = src->read(src, data, (size_t) size) == size;
	if (ok) {
		struct VFile* out = VFileOpen(path, O_CREAT | O_TRUNC | O_WRONLY);
		if (!out) ok = false;
		else {
			ok = out->write(out, data, (size_t) size) == size;
			out->close(out);
		}
	}
	free(data);
	src->seek(src, 0, SEEK_SET);
	return ok;
}

static uint64_t _eventCount(const struct GBAGBABREventStats* s, const char* type) {
	if (!type || !*type) return 0;
	if (!strcasecmp(type, "program")) return s->counts[GBA_GBABR_EVENT_NOR_PROGRAM];
	if (!strcasecmp(type, "erase")) return s->counts[GBA_GBABR_EVENT_NOR_ERASE];
	if (!strcasecmp(type, "fram")) return s->counts[GBA_GBABR_EVENT_FRAM_WRITE];
	if (!strcasecmp(type, "save")) return s->counts[GBA_GBABR_EVENT_NOR_PROGRAM] + s->counts[GBA_GBABR_EVENT_NOR_ERASE] + s->counts[GBA_GBABR_EVENT_FRAM_WRITE];
	if (!strcasecmp(type, "any")) return s->counts[GBA_GBABR_EVENT_NOR_PROGRAM] + s->counts[GBA_GBABR_EVENT_NOR_ERASE] + s->counts[GBA_GBABR_EVENT_FRAM_WRITE] + s->counts[GBA_GBABR_EVENT_MAPPER_CHANGE];
	return 0;
}

static void _writeReport(const char* path, bool pass, const char* reason, const struct GBAGBABRCart* cart,
                         const struct GBAGBABREventStats* s, uint32_t frames, bool coldResetDone, bool crashed) {
	FILE* f = path ? fopen(path, "w") : stdout;
	if (!f) return;
	fprintf(f,
	        "{\n"
	        "  \"schema\": 1,\n"
	        "  \"result\": \"%s\",\n"
	        "  \"reason\": \"%s\",\n"
	        "  \"profile\": \"%s\",\n"
	        "  \"capacity\": %zu,\n"
	        "  \"frames\": %u,\n"
	        "  \"cold_reset_performed\": %s,\n"
	        "  \"core_crashed\": %s,\n"
	        "  \"events\": {\n"
	        "    \"program\": %llu, \"erase\": %llu, \"fram\": %llu, \"mapper\": %llu,\n"
	        "    \"shadow_reads\": %llu, \"shadow_writes\": %llu,\n"
	        "    \"illegal_nor\": %llu, \"invalid_sequence\": %llu, \"native_save\": %llu\n"
	        "  },\n"
	        "  \"last\": {\"type\": %u, \"frame\": %u, \"pc\": %u, \"cpu_address\": %u, \"physical\": %u, \"value\": %u}\n"
	        "}\n",
	        pass ? "PASS" : "FAIL", reason ? reason : "", GBAGBABRProfileName(cart), GBAGBABRCapacity(cart), frames,
	        coldResetDone ? "true" : "false", crashed ? "true" : "false",
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_NOR_PROGRAM],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_NOR_ERASE],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_FRAM_WRITE],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_MAPPER_CHANGE],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_SAVE_SCRATCH_READ],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_SAVE_SCRATCH_WRITE],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_ILLEGAL_NOR_WRITE],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_INVALID_NOR_SEQUENCE],
	        (unsigned long long) s->counts[GBA_GBABR_EVENT_NATIVE_SAVE_ACCESS],
	        s->lastType, s->lastFrame, s->lastPc, s->lastCpuAddress, s->lastPhysicalAddress, s->lastValue);
	if (path) fclose(f);
}

int main(int argc, char** argv) {
	struct Options o;
	if (!_parseOptions(argc, argv, &o)) {
		_usage(argv[0]);
		return 2;
	}

	struct mCore* core = mCoreFind(o.rom);
	if (!core) {
		fprintf(stderr, "FAIL: not a recognized ROM: %s\n", o.rom);
		return 2;
	}
	if (!core->init(core)) {
		fprintf(stderr, "FAIL: core init\n");
		core->deinit(core);
		return 2;
	}
	mCoreInitConfig(core, "gbabr-autoqa");
	mCoreConfigSetDefaultValue(&core->config, "idleOptimization", "remove");
	mCoreLoadConfig(core);
	struct mCoreCallbacks cb = { .coreCrashed = _crashed, .shutdown = _shutdown };
	core->addCoreCallbacks(core, &cb);
	if (!mCoreLoadFile(core, o.rom)) {
		fprintf(stderr, "FAIL: could not load ROM\n");
		core->deinit(core);
		return 2;
	}
	core->reset(core);
	if (core->platform(core) != mPLATFORM_GBA) {
		fprintf(stderr, "FAIL: not GBA\n");
		core->unloadROM(core); core->deinit(core); return 2;
	}
	struct GBA* gba = core->board;
	if (gba->memory.unl.type != GBA_UNL_CART_GBABR || !gba->memory.unl.gbabr) {
		fprintf(stderr, "FAIL: mGBA virtual cart not active. Use mGBA tools or set the legacy MGBA_GBABR_PROFILE environment variable.\n");
		core->unloadROM(core); core->deinit(core); return 2;
	}
	struct GBAGBABRCart* cart = gba->memory.unl.gbabr;

	if (o.state) {
		if (o.stateKeepCart) setenv("MGBA_GBABR_KEEP_CART_ON_STATE_LOAD", "1", 1);
		struct VFile* vf = VFileOpen(o.state, O_RDONLY);
		if (!vf || !mCoreLoadStateNamed(core, vf, 0)) {
			if (vf) vf->close(vf);
			fprintf(stderr, "FAIL: could not load state %s\n", o.state);
			core->unloadROM(core); core->deinit(core); return 2;
		}
		vf->close(vf);
		if (o.stateKeepCart) unsetenv("MGBA_GBABR_KEEP_CART_ON_STATE_LOAD");
	}

	size_t movieCount = 0;
	struct MovieEntry* movie = _loadMovie(o.movie, &movieCount);
	if (o.movie && !movie) {
		fprintf(stderr, "FAIL: could not parse movie %s\n", o.movie);
		core->unloadROM(core); core->deinit(core); return 2;
	}

	const struct GBAGBABREventStats* stats = GBAGBABRGetEventStats(cart);
	uint64_t stopBaseline = _eventCount(stats, o.stopOn);
	uint32_t keys = 0;
	size_t moviePos = 0;
	bool eventSeen = false;
	bool coldResetDone = false;
	bool checkpointWriteFailed = false;
	uint32_t framesAfter = 0;
	uint32_t quietFrames = 0;
	uint32_t postResetFrames = 0;
	uint64_t lastStopCount = stopBaseline;
	uint32_t executed = 0;
	struct VFile* rollingCheckpoint = NULL;

	for (; executed < o.frames && !gCrashed && !gShutdown; ++executed) {
		if (o.preEventState && !eventSeen && executed % o.checkpointInterval == 0) {
			if (rollingCheckpoint) rollingCheckpoint->close(rollingCheckpoint);
			rollingCheckpoint = _captureState(core);
			if (!rollingCheckpoint) { checkpointWriteFailed = true; break; }
		}
		while (moviePos < movieCount && movie[moviePos].frame <= executed) {
			keys = movie[moviePos].keys;
			++moviePos;
		}
		core->setKeys(core, keys);
		core->runFrame(core);
		stats = GBAGBABRGetEventStats(cart);
		if (o.stopOn) {
			uint64_t nowStopCount = _eventCount(stats, o.stopOn);
			if (nowStopCount > stopBaseline && !eventSeen) {
				eventSeen = true;
				lastStopCount = nowStopCount;
				quietFrames = 0;
				if (o.preEventState && rollingCheckpoint && !_copyVFileToPath(rollingCheckpoint, o.preEventState)) { checkpointWriteFailed = true; break; }
				if (o.eventState && !_saveStateFile(core, o.eventState)) { checkpointWriteFailed = true; break; }
			}
			if (eventSeen && !coldResetDone) {
				if (nowStopCount != lastStopCount) { lastStopCount = nowStopCount; quietFrames = 0; }
				else ++quietFrames;
				if (o.coldResetOnEvent) {
					if (quietFrames >= o.afterEvent) {
						if (!GBAGBABRColdReset(gba, cart)) { gCrashed = true; break; }
						core->reset(core);
						coldResetDone = true;
						postResetFrames = 0;
					}
				} else if (++framesAfter >= o.afterEvent) break;
			}
			if (coldResetDone && ++postResetFrames >= o.postReset) break;
		}
	}
	free(movie);
	if (rollingCheckpoint) rollingCheckpoint->close(rollingCheckpoint);
	if (!checkpointWriteFailed && o.finalState && !_saveStateFile(core, o.finalState)) checkpointWriteFailed = true;
	stats = GBAGBABRGetEventStats(cart);

	bool pass = true;
	const char* reason = "completed";
	if (gCrashed) { pass = false; reason = "core crashed or cold reset failed"; }
	else if (checkpointWriteFailed) { pass = false; reason = "savestate/checkpoint write failed"; }
	else if (stats->counts[GBA_GBABR_EVENT_ILLEGAL_NOR_WRITE]) { pass = false; reason = "illegal NOR write"; }
	else if (stats->counts[GBA_GBABR_EVENT_INVALID_NOR_SEQUENCE]) { pass = false; reason = "invalid NOR command sequence"; }
	else if (stats->counts[GBA_GBABR_EVENT_NATIVE_SAVE_ACCESS] && !GBAGBABRNativeSaveAllowed(cart)) { pass = false; reason = "unexpected native save access"; }
	else if (o.require && !_eventCount(stats, o.require)) { pass = false; reason = "required event not observed"; }
	else if (o.stopOn && !eventSeen) { pass = false; reason = "stop event not observed"; }

	GBAGBABRFlush(cart);
	_writeReport(o.report, pass, reason, cart, stats, executed, coldResetDone, gCrashed);
	fprintf(stderr, "%s: %s | profile=%s frames=%u program=%llu erase=%llu fram=%llu illegal=%llu invalid=%llu native=%llu\n",
	        pass ? "PASS" : "FAIL", reason, GBAGBABRProfileName(cart), executed,
	        (unsigned long long) stats->counts[GBA_GBABR_EVENT_NOR_PROGRAM],
	        (unsigned long long) stats->counts[GBA_GBABR_EVENT_NOR_ERASE],
	        (unsigned long long) stats->counts[GBA_GBABR_EVENT_FRAM_WRITE],
	        (unsigned long long) stats->counts[GBA_GBABR_EVENT_ILLEGAL_NOR_WRITE],
	        (unsigned long long) stats->counts[GBA_GBABR_EVENT_INVALID_NOR_SEQUENCE],
	        (unsigned long long) stats->counts[GBA_GBABR_EVENT_NATIVE_SAVE_ACCESS]);
	core->unloadROM(core);
	mCoreConfigDeinit(&core->config);
	core->deinit(core);
	return pass ? 0 : 1;
}
