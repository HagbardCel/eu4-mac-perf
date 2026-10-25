#ifndef EU4_GFX_LIVE_CAPTURE_H
#define EU4_GFX_LIVE_CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

#define EU4_GFX_LIVE_CAPTURE_QUOTA 64u
#define EU4_GFX_LIVE_CAPTURE_THREADS 64u

bool eu4_gfx_live_capture_start(void *site, const char *run_dir,
                                const char *control_path,
                                const char *session_id,
                                unsigned deadline_seconds,
                                bool restore_on_stop);
/* Historical PR D quota path. The live entry point above is v2. */
bool eu4_gfx_live_capture_start_v1(void *site, const char *run_dir,
                                   const char *control_path,
                                   const char *session_id,
                                   unsigned deadline_seconds,
                                   bool restore_on_stop);
void eu4_gfx_live_capture_stop(void);

unsigned eu4_gfx_live_capture_test_lower_bound(uint32_t remaining_mask);
void eu4_gfx_live_capture_test_inject(const char *name);
void eu4_gfx_live_capture_test_arm(const char *point);
int eu4_gfx_live_capture_test_wait_arrived(const char *point, unsigned ms);
void eu4_gfx_live_capture_test_release(const char *point);
int eu4_gfx_live_capture_test_settle(unsigned ms);
int eu4_gfx_live_capture_test_invariant_ok(void);
void eu4_gfx_live_capture_test_diag(const char *scenario, const char *variant,
                                    const char *outcome);
uint64_t eu4_gfx_live_capture_test_counter(const char *name);
void eu4_gfx_live_capture_test_feasibility(uint64_t *retained, uint64_t *p_cov,
                                           uint64_t *p_div, uint64_t *lower,
                                           uint32_t *established);
int eu4_gfx_live_capture_test_find(uint32_t slot, uint32_t selector,
                                   uint32_t input_value);
int eu4_gfx_live_capture_test_signature(unsigned index, uint32_t *slot,
                                        uint32_t *selector, uint32_t *input,
                                        unsigned *state, int *reprobe,
                                        int *verify, uint64_t *encounter);
int eu4_gfx_live_capture_test_raw(unsigned index, uint32_t *slot,
                                  uint32_t *selector, uint32_t *input,
                                  unsigned *state, uint64_t *generation);
int eu4_gfx_live_capture_test_bump_generation(unsigned index);
void eu4_gfx_live_capture_test_drop_reservation(void);
void eu4_gfx_live_capture_test_synthetic_anchored_table(void);
void eu4_gfx_live_capture_test_predicate_label(const char *label);
int eu4_gfx_live_capture_test_screen_percentile(uint64_t *p50, uint64_t *p99,
                                                uint64_t *count);

#endif
